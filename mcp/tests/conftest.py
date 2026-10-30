"""A fake Cinema backend for the MCP tests.

A real HTTP server on an ephemeral port (not a mock transport) so the client's
real JSON/URL/error handling is exercised: status codes, ``detail`` strings and
the exact request bodies the tools send.  Every request is recorded on
``backend.calls`` so a test can assert what a tool actually posted.

The camera vocabulary is read from ``server/cinema/camera_vocab.json`` — the
same file the backend compiles prompts from — so a drift between the MCP
compiler and the studio's own table fails a test instead of shipping.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VOCAB_FILE = REPO_ROOT / "server" / "cinema" / "camera_vocab.json"

# The parity test imports the backend's own camera module (no install, no GPU);
# putting server/ on the path keeps plan_shot provably identical to the studio.
_SERVER_DIR = str(REPO_ROOT / "server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

LOOKS = [
    "none",
    "warm",
    "cool",
    "tealOrange",
    "bleachBypass",
    "fadedFilm",
    "dayForNight",
    "monoContrast",
    "crossProcess",
]
MODES = ("keyframe", "t2v", "i2v", "flf2v", "reshoot")


def _api_vocab() -> Dict[str, Any]:
    raw = json.loads(VOCAB_FILE.read_text("utf-8"))
    out: Dict[str, Any] = {"version": raw.get("version", 1)}
    for section in ("moves", "lenses", "speeds", "framings"):
        out[section] = [{"id": k, **v} for k, v in raw[section].items()]
    return out


class FakeBackend:
    """In-process backend with the routes the tools use."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, str, Optional[Dict[str, Any]]]] = []
        self.shots: Dict[str, Dict[str, Any]] = {}
        self.media: Dict[str, str] = {}
        self.grades: List[Dict[str, Any]] = []
        self.sequences: List[List[str]] = []
        self.sequence_bodies: List[Dict[str, Any]] = []
        self.comfy_reachable = True
        self._counter = 0

    # -- helpers used by the handler ---------------------------------------
    def next_id(self, prefix: str) -> str:
        self._counter += 1
        return "%s_%03d" % (prefix, self._counter)

    def record(self, method: str, path: str, body: Optional[Dict[str, Any]]) -> None:
        self.calls.append((method, path, body))

    def shots_in_order(self) -> List[Dict[str, Any]]:
        return list(self.shots.values())

    def posts(self, path: str) -> List[Optional[Dict[str, Any]]]:
        return [b for m, p, b in self.calls if m == "POST" and p == path]


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"

    def log_message(self, *args: Any) -> None:  # keep pytest output clean
        return

    # -- plumbing ----------------------------------------------------------
    def _send(self, status: int, payload: Any) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> Optional[Dict[str, Any]]:
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return None
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # -- routes ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        fake = self.server.fake
        path = urlparse(self.path).path
        fake.record("GET", path, None)

        if path == "/api/health":
            self._send(
                200,
                {
                    "ok": True,
                    "version": "0.1.0-test",
                    "comfy": {
                        "reachable": fake.comfy_reachable,
                        "base": "http://127.0.0.1:8188",
                        "version": "0.3.40" if fake.comfy_reachable else None,
                    },
                    "grade": {"available": True, "bin": "photocraft-cli"},
                },
            )
            return
        if path == "/api/camera/vocab":
            self._send(200, _api_vocab())
            return
        if path == "/api/grade/looks":
            self._send(200, [{"id": k, "label": k} for k in LOOKS])
            return
        if path == "/api/shots":
            self._send(200, fake.shots_in_order())
            return
        if path.startswith("/api/shots/"):
            shot = fake.shots.get(path.rsplit("/", 1)[-1])
            if shot is None:
                self._send(404, {"detail": "shot not found"})
            else:
                self._send(200, shot)
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            self._send(200, {"job_id": job_id, "status": "done", "progress": 1.0})
            return
        if path == "/api/jobs":
            self._send(200, [])
            return
        self._send(404, {"detail": "no route %s" % path})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        fake = self.server.fake
        path = urlparse(self.path).path
        body = self._body()
        fake.record("POST", path, body)

        if path == "/api/shots":
            self._create_shot(body or {})
            return
        if path.startswith("/api/shots/") and path.endswith("/grade"):
            shot_id = path.split("/")[3]
            shot = fake.shots.get(shot_id)
            if shot is None:
                self._send(404, {"detail": "shot not found"})
                return
            payload = body or {}
            look = payload.get("look", "none")
            strength = float(payload.get("strength", 1.0))
            if look not in LOOKS:
                self._send(400, {"detail": "unknown look %r" % look})
                return
            if not (0.0 <= strength <= 1.0):
                self._send(400, {"detail": "strength must be between 0 and 1"})
                return
            if not shot.get("media_id"):
                self._send(409, {"detail": "shot has no rendered media yet"})
                return
            fake.grades.append({"shot_id": shot_id, "look": look, "strength": strength})
            shot["graded_media_id"] = "m_graded"
            self._send(200, {"job_id": fake.next_id("j"), "shot_id": shot_id})
            return
        if path == "/api/sequence":
            ids = (body or {}).get("shot_ids") or []
            if not ids:
                self._send(400, {"detail": "shot_ids must not be empty"})
                return
            for sid in ids:
                if sid not in fake.shots:
                    self._send(400, {"detail": "shot %s not found" % sid})
                    return
            fake.sequences.append(list(ids))
            fake.sequence_bodies.append(dict(body or {}))
            self._send(200, {"job_id": fake.next_id("j")})
            return
        self._send(404, {"detail": "no route %s" % path})

    def _create_shot(self, body: Dict[str, Any]) -> None:
        fake = self.server.fake
        mode = body.get("mode")
        if mode not in MODES:
            self._send(400, {"detail": "unknown mode %r" % mode})
            return
        if mode in ("keyframe", "t2v") and not (body.get("prompt") or "").strip():
            self._send(400, {"detail": "prompt is required"})
            return
        if mode == "i2v" and not body.get("start_media_id"):
            self._send(400, {"detail": "i2v needs start_media_id"})
            return
        if mode == "flf2v" and not (body.get("start_media_id") and body.get("end_media_id")):
            self._send(400, {"detail": "flf2v needs start_media_id and end_media_id"})
            return
        if mode == "reshoot" and not (body.get("source_media_id") or body.get("start_media_id")):
            self._send(400, {"detail": "reshoot needs source_media_id"})
            return
        for field in ("start_media_id", "end_media_id", "source_media_id"):
            mid = body.get(field)
            if mid and mid not in fake.media:
                self._send(400, {"detail": "%s %r not found" % (field, mid)})
                return
        shot_id = fake.next_id("s")
        job_id = fake.next_id("j")
        fake.media["m_%s" % shot_id] = "video" if mode != "keyframe" else "image"
        fake.shots[shot_id] = {
            "shot_id": shot_id,
            "mode": mode,
            "prompt": body.get("prompt") or "",
            "negative": body.get("negative") or "",
            "camera": body.get("camera"),
            "quality": body.get("quality", "draft"),
            "seed": body.get("seed"),
            "start_media_id": body.get("start_media_id"),
            "end_media_id": body.get("end_media_id"),
            "source_media_id": body.get("source_media_id"),
            "at_s": body.get("at_s"),
            "status": "done",
            "progress": 1.0,
            "media_id": "m_%s" % shot_id,
            "graded_media_id": None,
            "error": None,
            "compiled_prompt": "compiled",
            "graph": {"1": {"class_type": "Fake", "inputs": {}}},
            "job_id": job_id,
        }
        self._send(200, {"shot_id": shot_id, "job_id": job_id})


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, fake: FakeBackend) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.fake = fake


@pytest.fixture()
def backend() -> Any:
    fake = FakeBackend()
    server = _Server(fake)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake.url = "http://127.0.0.1:%d" % server.server_address[1]  # type: ignore[attr-defined]
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def client(backend: FakeBackend) -> Any:
    from cinema_mcp.client import CinemaClient

    return CinemaClient(url=backend.url)
