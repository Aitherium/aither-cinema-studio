"""ComfyUI HTTP client - self-hosted *and* Comfy Cloud.

Two surfaces share one API-format payload (``{node_id: {class_type, inputs}}``):

* ``local``  - ``http://127.0.0.1:8188``, no auth, ``/prompt`` + ``/history``.
* ``cloud``  - ``https://cloud.comfy.org``, ``X-API-Key``, **every path prefixed with
  ``/api``**, job state read from ``/api/jobs/{id}`` (there is no ``/history``), and
  ``/api/view`` answering ``302`` to a signed storage URL.

``COMFY_SURFACE=auto|cloud|local`` (default ``auto``) picks the surface: ``auto`` means
cloud when the base URL contains ``comfy.org``.  The API key is resolved from
``COMFY_API_KEY``, else the file named by ``COMFY_API_KEY_FILE``, else
``$CINEMA_DATA_DIR/settings.json`` - and is never rendered by ``repr()``, never placed in
an error message, and never sent to the storage host that ``/api/view`` redirects to.
**It is also pinned to the host it was saved for** (``api_key_host``): a client whose
``base_url`` names another host sends no ``X-API-Key``, so repointing the backend cannot
carry a key off the box.

Submission blocks while the server is busy, hence the 300 s submit timeout.  When the
client gives up it asks the server to drop the prompt from its queue
(``POST /queue {"delete": [pid]}``) so the job does not keep a GPU busy for nothing.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

CLOUD_BASE = "https://cloud.comfy.org"
LOCAL_BASE = "http://127.0.0.1:8188"
SURFACES = ("auto", "cloud", "local")

#: Where a key goes when nothing better is known ("the default allowlisted host").
DEFAULT_KEY_HOST = "cloud.comfy.org"


def url_host(value: Optional[str]) -> Optional[str]:
    """The lowercase hostname of a URL, or None when it cannot be read."""
    if not value:
        return None
    try:
        host = urlparse(str(value).strip()).hostname
    except ValueError:
        return None
    host = (host or "").strip().lower()
    return host or None

# Comfy Cloud job states.  Three sources disagree on the spelling (docs prose vs
# openapi `JobStatusResponse` vs `JobDetailResponse`), so poll on the sets.
CLOUD_DONE = {"success", "completed"}
CLOUD_FAILED = {"error", "failed", "non_retryable_error", "lost", "cancelled"}

SETTINGS_FILE = "settings.json"


class ComfyError(RuntimeError):
    """ComfyUI returned an error (or an unusable response)."""


class ComfyTimeout(ComfyError):
    """The client gave up waiting for a prompt; it has been dequeued."""


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _read_key_file(path: str) -> Optional[str]:
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError:
        return None
    text = text.strip()
    return text or None


def settings_path(data_dir: Optional[Any] = None) -> Path:
    """Where the UI-written settings live (``CINEMA_DATA_DIR/settings.json``)."""
    base = data_dir or os.environ.get("CINEMA_DATA_DIR") or "./data"
    return Path(base) / SETTINGS_FILE


def load_stored_settings(data_dir: Optional[Any] = None) -> Dict[str, Any]:
    path = settings_path(data_dir)
    try:
        with path.open("r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def save_stored_settings(data_dir: Optional[Any] = None, **fields: Any) -> Dict[str, Any]:
    """Merge ``fields`` into settings.json; the key is written 0600 where possible."""
    path = settings_path(data_dir)
    doc = load_stored_settings(data_dir)
    for name, value in fields.items():
        if value is None:
            doc.pop(name, None)
        else:
            doc[name] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    try:
        os.chmod(tmp, 0o600)
    except OSError:  # pragma: no cover - windows / exotic filesystems
        pass
    os.replace(tmp, path)
    return doc


def mask_key(key: Optional[str]) -> str:
    """The only shape a key may take in a response or a log line."""
    if not key:
        return ""
    return "..." + key[-4:] if len(key) > 8 else "***"


@dataclass
class Settings:
    surface: str = "local"
    base_url: str = LOCAL_BASE
    api_key: Optional[str] = field(default=None, repr=False)
    #: The host the key was saved for. It is only sent back to this host.
    api_key_host: Optional[str] = field(default=None, repr=False)
    api_key_source: str = "none"
    ca_bundle: Optional[str] = None
    submit_timeout: float = 300.0

    @property
    def is_cloud(self) -> bool:
        return self.surface == "cloud"

    @property
    def key_set(self) -> bool:
        return bool(self.api_key)

    @property
    def key_hint(self) -> str:
        return mask_key(self.api_key)

    @classmethod
    def resolve(
        cls,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        surface: Optional[str] = None,
        data_dir: Optional[Any] = None,
    ) -> "Settings":
        """Explicit argument > environment > ``data_dir/settings.json`` > defaults."""
        stored = load_stored_settings(data_dir)
        want = (surface or os.environ.get("COMFY_SURFACE") or "auto").strip().lower()
        if want not in SURFACES:
            want = "auto"

        raw_base = (
            base_url
            or os.environ.get("COMFY_BASE_URL")
            or stored.get("base_url")
            or (CLOUD_BASE if want == "cloud" else LOCAL_BASE)
        )
        raw_base = str(raw_base).rstrip("/") or LOCAL_BASE
        resolved = "cloud" if want == "auto" and "comfy.org" in raw_base else want
        if resolved == "auto":  # explicit auto on a local-style URL
            resolved = "local"

        key: Optional[str] = None
        source = "none"
        key_host: Optional[str] = None
        stored_host = url_host(stored.get("api_key_host"))
        stored_base = stored.get("base_url")
        if api_key:
            # A key handed in explicitly (in-process, or after an authenticated
            # settings test) belongs to the host it is being used with.
            key, source = api_key, "argument"
            key_host = (
                url_host(base_url)
                or url_host(os.environ.get("COMFY_BASE_URL"))
                or DEFAULT_KEY_HOST
            )
        elif os.environ.get("COMFY_API_KEY"):
            key, source = os.environ["COMFY_API_KEY"], "env"
            key_host = (
                url_host(os.environ.get("COMFY_BASE_URL"))
                or url_host(base_url)
                or DEFAULT_KEY_HOST
            )
        elif os.environ.get("COMFY_API_KEY_FILE"):
            key = _read_key_file(os.environ["COMFY_API_KEY_FILE"])
            source = "file" if key else "file-missing"
            if key:
                key_host = (
                    url_host(os.environ.get("COMFY_BASE_URL"))
                    or url_host(base_url)
                    or DEFAULT_KEY_HOST
                )
        elif stored.get("api_key"):
            key, source = str(stored["api_key"]), "settings"
            # Never follow a caller-supplied base_url: the key stays with the
            # host it was saved for. Legacy files without api_key_host keep the
            # host of their stored base_url, else the cloud default.
            key_host = stored_host or url_host(stored_base) or DEFAULT_KEY_HOST

        return cls(
            surface=resolved,
            base_url=raw_base,
            api_key=key,
            api_key_host=key_host,
            api_key_source=source,
            ca_bundle=os.environ.get("COMFY_CA_BUNDLE") or None,
            submit_timeout=_env_float("COMFY_SUBMIT_TIMEOUT_S", 300.0),
        )

    @classmethod
    def from_env(cls, data_dir: Optional[Any] = None) -> "Settings":
        return cls.resolve(data_dir=data_dir)

    def public(self) -> Dict[str, Any]:
        """A dict safe to return from an HTTP route: never the key itself."""
        return {
            "surface": self.surface,
            "base_url": self.base_url,
            "key_set": self.key_set,
            "key_hint": self.key_hint,
            "key_host": self.api_key_host,
            "key_source": self.api_key_source,
        }


class ComfyClient:
    """Minimal, synchronous ComfyUI client (one shot = upload/prompt/wait/view)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        ca_bundle: Optional[str] = None,
        submit_timeout: Optional[float] = None,
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
        client_id: Optional[str] = None,
        surface: Optional[str] = None,
        data_dir: Optional[Any] = None,
    ) -> None:
        settings = Settings.resolve(
            base_url=base_url, api_key=api_key, surface=surface, data_dir=data_dir
        )
        self.settings = settings
        self.surface = settings.surface
        self.cloud = settings.is_cloud
        self.base_url = settings.base_url
        self.api_key = settings.api_key
        self.api_key_host = settings.api_key_host
        self.ca_bundle = ca_bundle if ca_bundle is not None else settings.ca_bundle
        self.submit_timeout = (
            submit_timeout if submit_timeout is not None else settings.submit_timeout
        )
        self.client_id = client_id or uuid.uuid4().hex
        self._timeout = timeout
        self._client = self._make_client(transport)

    def __repr__(self) -> str:
        return "ComfyClient(base_url=%r, surface=%r, api_key=%s)" % (
            self.base_url,
            self.surface,
            "<set>" if self.api_key else "None",
        )

    __str__ = __repr__

    @property
    def prefix(self) -> str:
        """Every cloud path lives under ``/api``; local paths are unprefixed."""
        return "/api" if self.cloud else ""

    # -- plumbing ---------------------------------------------------------
    def _make_client(self, transport: Optional[httpx.BaseTransport]) -> httpx.Client:
        # Redirects are never followed: against cloud, ``/api/view`` answers 302
        # to a storage host that must NOT receive ``X-API-Key``.
        kwargs: Dict[str, Any] = {"timeout": self._timeout, "follow_redirects": False}
        if transport is not None:
            # A custom transport owns the connection; verify is only used to
            # build the default one.
            kwargs["transport"] = transport
        else:
            kwargs["verify"] = self.ca_bundle if self.ca_bundle else True
        return httpx.Client(**kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ComfyClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def redact(self, text: str) -> str:
        """Strip the API key out of anything that may reach a log or an error."""
        key = self.api_key
        if key and key in text:
            text = text.replace(key, "<redacted>")
        return text

    @property
    def key_allowed(self) -> bool:
        """True only while the request host is the host the key was saved for."""
        if not self.api_key:
            return False
        bound = (self.api_key_host or DEFAULT_KEY_HOST).strip().lower()
        return url_host(self.base_url) == bound

    @property
    def headers(self) -> Dict[str, str]:
        h = {"Accept": "application/json"}
        # The key goes to its own host and nowhere else: a repointed base_url
        # must never receive a key that was saved for another server.
        if self.key_allowed:
            h["X-API-Key"] = self.api_key  # type: ignore[assignment]
        return h

    def _url(self, path: str) -> str:
        return self.base_url + (path if path.startswith("/") else "/" + path)

    def _api(self, path: str) -> str:
        return self._url(self.prefix + (path if path.startswith("/") else "/" + path))

    def _json(self, method: str, path: str, *, timeout: Optional[float] = None, **kw: Any) -> Any:
        resp = self._client.request(
            method, self._api(path), headers=self.headers, timeout=timeout, **kw
        )
        if resp.status_code >= 400:
            raise ComfyError(
                self.redact(
                    "ComfyUI %s %s -> HTTP %s: %s"
                    % (method, path, resp.status_code, resp.text[:400])
                )
            )
        try:
            return resp.json()
        except ValueError as exc:  # pragma: no cover - defensive
            raise ComfyError("ComfyUI %s %s returned non-JSON" % (method, path)) from exc

    # -- discovery --------------------------------------------------------
    def object_info(self) -> Dict[str, Any]:
        """Full node registry.  Large (several MB) - call once and cache."""
        return self._json("GET", "/object_info", timeout=60.0)

    def system_stats(self) -> Dict[str, Any]:
        return self._json("GET", "/system_stats", timeout=15.0)

    def version(self) -> Optional[str]:
        try:
            stats = self.system_stats()
        except (httpx.HTTPError, ComfyError):
            return None
        system = stats.get("system") if isinstance(stats, dict) else None
        if isinstance(system, dict):
            return system.get("comfyui_version") or system.get("version")
        return None

    def user(self) -> Dict[str, Any]:
        """Cloud only: the authenticated account.  Never returns the key."""
        doc = self._json("GET", "/user", timeout=20.0)
        return doc if isinstance(doc, dict) else {}

    def test_credentials(self) -> Dict[str, Any]:
        """Probe auth: ``/user`` on cloud, ``/system_stats`` on a local server.

        The returned dict NEVER carries the key - only its presence and the
        challenge's status code.
        """
        out: Dict[str, Any] = {
            "ok": False,
            "surface": self.surface,
            "base_url": self.base_url,
            "key_set": bool(self.api_key),
            # False when the stored key belongs to another host - it is not sent.
            "key_sent": self.key_allowed,
            "status": None,
            "error": None,
        }
        try:
            if self.cloud:
                doc = self.user()
                out["status"] = 200
                for name in ("name", "email", "username", "id"):
                    if doc.get(name):
                        out["account"] = str(doc[name])[:80]
                        break
            else:
                self.system_stats()
                out["status"] = 200
            out["ok"] = True
        except httpx.HTTPError as exc:
            out["error"] = self.redact(str(exc)) or exc.__class__.__name__
        except ComfyError as exc:
            text = self.redact(str(exc))
            out["error"] = text
            for code in (401, 402, 403, 429):
                if "HTTP %d" % code in text:
                    out["status"] = code
                    break
        return out

    def reachable(self) -> bool:
        """Cloud has no guarantee of ``/queue``; ``/system_stats`` is public there."""
        try:
            if self.cloud:
                self.system_stats()
            else:
                self.queue()
            return True
        except (httpx.HTTPError, ComfyError):
            return False

    # -- upload -----------------------------------------------------------
    def upload_image(self, data: bytes, filename: str, subfolder: str = "") -> str:
        """Upload a PNG/JPG into ComfyUI's input dir; return the LoadImage name."""
        files = {"image": (filename, data, "image/png")}
        form = {"overwrite": "true", "type": "input"}
        if subfolder:
            form["subfolder"] = subfolder
        resp = self._client.post(
            self._api("/upload/image"),
            headers=self.headers,
            files=files,
            data=form,
            timeout=120.0,
        )
        if resp.status_code >= 400:
            raise ComfyError(
                self.redact(
                    "ComfyUI upload failed: HTTP %s %s" % (resp.status_code, resp.text[:300])
                )
            )
        info = resp.json()
        name = info.get("name") or filename
        sub = info.get("subfolder") or ""  # accepted and ignored by cloud
        return "%s/%s" % (sub, name) if sub else name

    # -- queue ------------------------------------------------------------
    def submit(self, graph: Dict[str, Any], client_id: Optional[str] = None) -> str:
        """POST /prompt (blocks while busy -> the long submit timeout)."""
        clean = {k: v for k, v in graph.items() if not str(k).startswith("_")}
        payload: Dict[str, Any] = {"prompt": clean}
        # ``client_id`` is a local-queue field; the cloud schema does not list it.
        if not self.cloud:
            payload["client_id"] = client_id or self.client_id
        try:
            info = self._json("POST", "/prompt", json=payload, timeout=self.submit_timeout)
        except httpx.TimeoutException as exc:
            raise ComfyTimeout(
                "ComfyUI did not accept the prompt within %.0fs" % self.submit_timeout
            ) from exc
        if not isinstance(info, dict):
            raise ComfyError("unexpected /prompt response")
        node_errors = info.get("node_errors") or {}
        if node_errors or info.get("error"):
            raise ComfyError(
                self.redact(
                    "ComfyUI rejected the graph: %s"
                    % _compact(info.get("error") or node_errors)
                )
            )
        pid = info.get("prompt_id")
        if not pid:
            raise ComfyError(self.redact("ComfyUI returned no prompt_id: %s" % _compact(info)))
        return str(pid)

    def history(self, prompt_id: str) -> Dict[str, Any]:
        if self.cloud:
            # Local-shaped ``{prompt_id: {prompt, outputs, status, meta}}``.
            doc = self._json("GET", "/history_v2/%s" % prompt_id)
            return doc if isinstance(doc, dict) else {}
        return self._json("GET", "/history/%s" % prompt_id)

    def queue(self) -> Dict[str, Any]:
        return self._json("GET", "/queue")

    def cancel(self, prompt_id: str) -> bool:
        """Drop a prompt from the queue.  Returns False if the server refused."""
        try:
            self._json("POST", "/queue", json={"delete": [prompt_id]})
            return True
        except (httpx.HTTPError, ComfyError):
            return False

    def queue_state(self, prompt_id: str) -> str:
        """'running', 'queued' or '' when the prompt is in neither list."""
        if self.cloud:
            return ""
        try:
            q = self.queue()
        except (httpx.HTTPError, ComfyError):
            return ""
        for key, label in (("queue_running", "running"), ("queue_pending", "queued")):
            for item in q.get(key) or []:
                if isinstance(item, (list, tuple)) and len(item) > 1 and str(item[1]) == prompt_id:
                    return label
        return ""

    # -- cloud job state --------------------------------------------------
    def job(self, job_id: str) -> Dict[str, Any]:
        """``GET /api/jobs/{id}`` - the one call cloud needs (outputs included)."""
        doc = self._json("GET", "/jobs/%s" % job_id, timeout=60.0)
        return doc if isinstance(doc, dict) else {}

    @staticmethod
    def job_state(job: Dict[str, Any]) -> str:
        """'done' | 'failed' | 'running' - from the terminal-set rule."""
        raw = str((job or {}).get("status") or "").strip().lower()
        if raw in CLOUD_DONE:
            return "done"
        if raw in CLOUD_FAILED:
            return "failed"
        return "running"

    def _job_entry(self, job_id: str) -> Optional[Dict[str, Any]]:
        try:
            job = self.job(job_id)
        except (httpx.HTTPError, ComfyError):
            return None  # not registered yet, or a transient 5xx: keep polling
        state = self.job_state(job)
        if state == "failed":
            detail = job.get("error_message") or job.get("execution_error") or job.get("status")
            raise ComfyError(
                self.redact(
                    "Comfy Cloud job %s failed: %s" % (job_id, _compact(detail))
                )
            )
        if state != "done":
            return None
        outputs = job.get("outputs") or {}
        if isinstance(outputs, list):  # tolerate a flattened listing
            outputs = {}
        return {
            "outputs": outputs,
            "status": {
                "status_str": "success",
                "completed": True,
                "messages": job.get("execution_error") or [],
            },
            "job": job,
        }

    # -- results ----------------------------------------------------------
    @staticmethod
    def entry_from_history(history: Dict[str, Any], prompt_id: str) -> Optional[Dict[str, Any]]:
        entry = history.get(prompt_id) if isinstance(history, dict) else None
        return entry if isinstance(entry, dict) else None

    @staticmethod
    def collect_outputs(entry: Dict[str, Any]) -> List[Dict[str, str]]:
        """Flatten a history entry's outputs.  Reads images, gifs and videos."""
        out: List[Dict[str, str]] = []
        outputs = (entry or {}).get("outputs") or {}
        if not isinstance(outputs, dict):
            return out
        for node_id in outputs:
            node_out = outputs[node_id] or {}
            if not isinstance(node_out, dict):
                continue
            for key in ("videos", "gifs", "images"):
                for item in node_out.get(key) or []:
                    if not isinstance(item, dict) or not item.get("filename"):
                        continue
                    out.append(
                        {
                            "kind": "video" if key in ("videos", "gifs") else "image",
                            "filename": item.get("filename", ""),
                            "subfolder": item.get("subfolder", "") or "",
                            "type": item.get("type", "output") or "output",
                            "node": str(node_id),
                        }
                    )
        return out

    def download(self, output: Dict[str, str]) -> bytes:
        """Bytes for one output.  Cloud answers 302 to a signed URL - fetch it bare."""
        params = {
            "filename": output.get("filename", ""),
            "subfolder": output.get("subfolder", ""),
            "type": output.get("type", "output"),
        }
        resp = self._client.get(
            self._api("/view"),
            params=params,
            headers=self.headers,
            timeout=300.0,
            follow_redirects=False,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location") or resp.headers.get("Location")
            if not location:
                raise ComfyError("ComfyUI /view redirected with no Location")
            # No headers, no key: the storage host is not a ComfyUI endpoint.
            fetch = self._client.get(location, headers={}, timeout=300.0)
            if fetch.status_code >= 400:
                raise ComfyError("ComfyUI output fetch failed: HTTP %s" % fetch.status_code)
            return fetch.content
        if resp.status_code >= 400:
            raise ComfyError(
                self.redact("ComfyUI /view failed: HTTP %s %s" % (resp.status_code, resp.text[:200]))
            )
        return resp.content

    # -- orchestration ----------------------------------------------------
    def status_entry(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        """A local-shaped entry once the prompt is finished, else None."""
        if self.cloud:
            return self._job_entry(prompt_id)
        try:
            entry = self.entry_from_history(self.history(prompt_id), prompt_id)
        except (httpx.HTTPError, ComfyError):
            return None
        if entry is None:
            return None
        status = entry.get("status") or {}
        if isinstance(status, dict) and status.get("status_str") == "error":
            raise ComfyError(
                self.redact(
                    "ComfyUI execution error: %s" % _compact(status.get("messages"))
                )
            )
        outputs = self.collect_outputs(entry)
        if outputs or (isinstance(status, dict) and status.get("completed")):
            return entry
        return None

    def wait(
        self,
        prompt_id: str,
        timeout: float = 3600.0,
        interval: float = 1.0,
        on_status: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Poll until the prompt finishes.  On give-up: dequeue, then raise."""
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                entry = self.status_entry(prompt_id)
            except ComfyError:
                raise
            except httpx.HTTPError:
                entry = None
            if entry is not None:
                return entry
            state = self.queue_state(prompt_id)
            if state and state != last:
                last = state
                if on_status is not None:
                    on_status(state)
            time.sleep(interval)
        self.cancel(prompt_id)
        raise ComfyTimeout(
            "gave up waiting for prompt %s after %.0fs (dequeued)" % (prompt_id, timeout)
        )

    def run(
        self,
        graph: Dict[str, Any],
        timeout: float = 3600.0,
        interval: float = 1.0,
        on_status: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Submit + wait.  Returns {"prompt_id", "outputs", "entry"}."""
        pid = self.submit(graph)
        if on_status is not None:
            on_status("submitted")
        try:
            entry = self.wait(pid, timeout=timeout, interval=interval, on_status=on_status)
        except ComfyError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self.cancel(pid)
            raise ComfyError(self.redact("prompt %s failed: %s" % (pid, exc))) from None
        return {"prompt_id": pid, "outputs": self.collect_outputs(entry), "entry": entry}


def _compact(value: Any, limit: int = 500) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "..."
