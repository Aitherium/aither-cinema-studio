"""Hold a GPU lease for a render, so a power manager cannot evict ComfyUI mid-job.

On a machine with no fleet door this module does nothing: a user's own ComfyUI is theirs.
On a host that arbitrates the GPU (the AitherOS fleet: MicroScheduler's ``/gpu/lease``),
a render without a lease is evictable -- measured 2026-10-31, ComfyUI was stopped 23
minutes into a Wan render because the unit "does not outrank" whatever asked next.

Knobs (environment, or KEY=VALUE in cinema.env):
  COMFY_LEASE            auto (default; only when a key is available) | 1 | 0
  COMFY_LEASE_URL        default https://127.0.0.1:8150/gpu/lease
  COMFY_LEASE_KEY_FILE   default ~/.aither/internal_key.txt
  AITHER_INTERNAL_SECRET the key itself, when set, wins over the file
  COMFY_LEASE_CA         a CA bundle for the door's TLS (default: the fleet chain, if present)
  COMFY_LEASE_VRAM_MB    what the render asks for, default 14000 (Wan 14B fp8 + encoder; 16000 was refused against 15.7 GiB free)
  COMFY_LEASE_CONSUMER   consumer id, default aither-cinema

A refusal from the door FAILS the render with the door's reason. A door that cannot be
reached proceeds UNLEASED and says so: a self-hosted ComfyUI has no door and must still
render. Standard library only.
"""
from __future__ import annotations

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_URL = "https://127.0.0.1:8150/gpu/lease"
DEFAULT_CA = Path(r"C:\AitherOS-Data\Library\Data\tls\ca-chain.pem")
CLASS = "interactive_media"


class LeaseRefused(Exception):
    """The door answered and said no. The render must not start."""


def _key_file() -> Path:
    return Path(os.environ.get("COMFY_LEASE_KEY_FILE")
                or Path.home() / ".aither" / "internal_key.txt")


def key() -> str:
    env = os.environ.get("AITHER_INTERNAL_SECRET", "").strip()
    if env:
        return env
    path = _key_file()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def enabled() -> bool:
    mode = os.environ.get("COMFY_LEASE", "auto").strip().lower()
    if mode in ("0", "false", "off", "no"):
        return False
    if mode in ("1", "true", "on", "yes"):
        return True
    return bool(key())  # auto


def _ctx() -> Optional[ssl.SSLContext]:
    ca = os.environ.get("COMFY_LEASE_CA")
    if not ca and DEFAULT_CA.is_file():
        ca = str(DEFAULT_CA)
    if not ca:
        return None
    return ssl.create_default_context(cafile=ca)


def _call(path: str, body: Dict[str, Any], timeout: float = 120.0) -> Dict[str, Any]:
    url = os.environ.get("COMFY_LEASE_URL", DEFAULT_URL).rstrip("/") + path
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Internal-Key": key()})
    ctx = _ctx()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read() or b"{}")


class FleetLease:
    """A held lease. ``enter`` acquires, a thread heartbeats, ``exit`` releases."""

    def __init__(self, job_ref: str = "", vram_mb: Optional[int] = None,
                 ttl_s: int = 900) -> None:
        self.job_ref = job_ref
        self.vram_mb = int(vram_mb or os.environ.get("COMFY_LEASE_VRAM_MB") or 14000)
        self.ttl_s = int(ttl_s)
        self.token: str = ""
        self.note: str = ""
        self._stop = threading.Event()
        self._beat: Optional[threading.Thread] = None

    # -- context manager --------------------------------------------------
    def __enter__(self) -> "FleetLease":
        if not enabled():
            return self
        body = {"class": CLASS, "vram_mb": self.vram_mb, "ttl_s": self.ttl_s,
                "consumer_id": os.environ.get("COMFY_LEASE_CONSUMER", "aither-cinema"),
                "job_ref": self.job_ref, "wait_s": 60, "give_up_s": 120}
        try:
            reply = _call("/acquire", body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise LeaseRefused(f"the GPU is booked: {detail or exc.code}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.note = f"no GPU lease ({exc.__class__.__name__}); rendering unleased"
            return self
        self.token = str(reply.get("token") or "")
        if not self.token:
            raise LeaseRefused("the lease door answered without a token")
        self._beat = threading.Thread(target=self._heartbeat, name="cinema-lease", daemon=True)
        self._beat.start()
        return self

    def __exit__(self, *exc: Any) -> bool:
        self._stop.set()
        if self.token:
            try:
                _call("/release", {"token": self.token, "outcome": "ok"}, timeout=20)
            except Exception:  # noqa: BLE001 - release is best effort; the TTL covers it
                pass
        return False

    def _heartbeat(self) -> None:
        every = max(30, self.ttl_s // 3)
        while not self._stop.wait(every):
            try:
                _call("/heartbeat", {"token": self.token}, timeout=20)
            except Exception:  # noqa: BLE001 - a missed beat is retried; the TTL is the guard
                continue


def hold(job_ref: str = "", vram_mb: Optional[int] = None, ttl_s: int = 900) -> FleetLease:
    return FleetLease(job_ref=job_ref, vram_mb=vram_mb, ttl_s=ttl_s)


def status() -> Dict[str, Any]:
    return {"enabled": enabled(), "key": bool(key()),
            "url": os.environ.get("COMFY_LEASE_URL", DEFAULT_URL)}


__all__ = ["FleetLease", "LeaseRefused", "enabled", "hold", "status", "key"]
_ = time  # kept for callers that time a lease by hand
