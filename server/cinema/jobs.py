"""Job store and background worker.

A job is a small JSON record (``status``/``progress``/``result``/``error``)
persisted under ``$CINEMA_DATA_DIR/jobs.json``.  The *runner* is a Python
callable and therefore lives in memory only: after a restart, jobs that were
``queued`` or ``running`` are marked ``error`` ("interrupted") rather than
silently hanging the UI.

One worker thread by default - this host is memory-tight, and ComfyUI is the
real bottleneck anyway.  ``run_inline=True`` executes the runner synchronously
inside :meth:`create`, which is what the tests use to stay deterministic.
"""

from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .media import new_id, now_iso

Runner = Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]]

TERMINAL = ("done", "error")


class JsonStore:
    """A thread-safe ``id -> record`` map backed by one JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._items: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_locked(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._items, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    # -- API --------------------------------------------------------------
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            rec = self._items.get(key)
            return dict(rec) if rec else None

    def all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._items.values()]

    def put(self, key: str, record: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._items[key] = dict(record)
            self._save_locked()
            return dict(self._items[key])

    def update(self, key: str, **fields: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            rec = self._items.get(key)
            if rec is None:
                return None
            rec.update(fields)
            rec["updated"] = now_iso()
            self._save_locked()
            return dict(rec)

    def delete(self, key: str) -> bool:
        with self._lock:
            if key not in self._items:
                return False
            del self._items[key]
            self._save_locked()
            return True


class JobStore:
    def __init__(self, data_dir: Path, inline: bool = False, shots: Any = None) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.inline = inline
        self.jobs = JsonStore(self.data_dir / "jobs.json")
        # the SAME store the state exposes, so a job's outcome reaches the shot the board shows
        self.shots = shots if shots is not None else JsonStore(self.data_dir / "shots.json")
        self._shots: Dict[str, set] = {}
        self._lock = threading.Lock()
        self._queue: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._recover()

    def _recover(self) -> None:
        for rec in self.jobs.all():
            if rec.get("status") not in TERMINAL:
                self.jobs.update(
                    rec["job_id"],
                    status="error",
                    error="interrupted by server restart",
                )
                rec = {**rec, "status": "error", "error": "interrupted by server restart"}
            shot_id = (rec.get("params") or {}).get("shot_id")
            # Repair the split left by the pre-10-31 build: a terminal job whose shot still
            # reads running. Without this the board shows 0% for ever.
            if shot_id and rec.get("status") == "error":
                shot = self.shots.get(shot_id) or {}
                if shot.get("status") in ("queued", "running"):
                    self.shots.update(
                        shot_id, status="error", progress=1.0,
                        error=rec.get("error") or "render failed",
                    )

    # -- lifecycle --------------------------------------------------------
    def start(self) -> "JobStore":
        if self.inline or (self._thread and self._thread.is_alive()):
            return self
        self._thread = threading.Thread(target=self._loop, name="cinema-jobs", daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            if item is None:
                break
            job_id, runner = item
            self._execute(job_id, runner)

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # -- API --------------------------------------------------------------
    def create(
        self,
        kind: str,
        runner: Runner,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        job_id = new_id("j")
        record = {
            "job_id": job_id,
            "kind": kind,
            "status": "queued",
            "progress": 0.0,
            "params": params or {},
            "result": None,
            "error": None,
            "created": now_iso(),
            "updated": now_iso(),
        }
        self.jobs.put(job_id, record)
        if params and params.get("shot_id"):
            self.shots.update(params["shot_id"], job_id=job_id)
        with self._lock:
            self._shots.setdefault(job_id, set()).add((params or {}).get("shot_id") or "")
        if self.inline:
            self._execute(job_id, runner)
        else:
            self._queue.put((job_id, runner))
            self.start()
        return self.jobs.get(job_id) or record

    def _mark_shot(self, job_id: str, **fields: Any) -> None:
        """Mirror a job's terminal state onto its shot.

        The board reads only the shot. A job that errored at once used to leave the shot
        ``running`` at 0% for ever (2026-10-30): the shot was never told."""
        with self._lock:
            shot_ids = {s for s in self._shots.pop(job_id, set()) if s}
        for shot_id in shot_ids:
            self.shots.update(shot_id, **fields)

    def cancel(self, job_id: str) -> bool:
        """Mark a job cancelled. A queued one never runs; a running one stops being reported."""
        rec = self.jobs.get(job_id) or {}
        if rec.get("status") not in ("queued", "running"):
            return False
        self.jobs.update(job_id, status="cancelled", progress=1.0)
        self._mark_shot(job_id, status="cancelled", error="cancelled by you")
        return True

    def _execute(self, job_id: str, runner: Runner) -> None:
        record = self.jobs.get(job_id) or {"job_id": job_id}
        self.jobs.update(job_id, status="running", progress=0.01)
        try:
            result = runner(job_id, record)
        except BaseException as exc:  # noqa: BLE001 - a job must never kill the worker
            self.jobs.update(job_id, status="error", error=_message(exc), progress=1.0)
            self._mark_shot(job_id, status="error", error=_message(exc), progress=1.0)
            return
        fields: Dict[str, Any] = {"status": "done", "progress": 1.0, "error": None}
        if isinstance(result, dict):
            fields["result"] = result
        self.jobs.update(job_id, **fields)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            self._shots.pop(job_id, None)
        return self.jobs.delete(job_id)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.jobs.get(job_id)

    def list(self) -> List[Dict[str, Any]]:
        return self.jobs.all()

    def progress(self, job_id: str, value: float) -> None:
        self.jobs.update(job_id, progress=max(0.0, min(1.0, float(value))))


def _message(exc: BaseException) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__
