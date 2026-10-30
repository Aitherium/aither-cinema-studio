"""Settings-write authorisation and host binding for the backend.

The bug this module closes: ``PUT /api/settings`` used to be open to anyone who
could reach the port, and ``ComfyClient`` attached the stored ``X-API-Key`` to
whatever ``base_url`` was set - so one unauthenticated request could repoint the
backend and receive the victim's Comfy Cloud key.

Two rules, both enforced here / in :mod:`cinema.comfy`:

1. A settings write (``PUT /api/settings``, ``POST /api/settings/test`` *with a
   body*) is allowed only for:

   * a loopback client making a same-origin or allowlisted-origin request, or
   * any client presenting ``Authorization: Bearer <CINEMA_TOKEN>``.

   When the server binds a non-loopback host and ``CINEMA_TOKEN`` is unset, one
   is generated at startup and printed once so the operator can use it.

2. The stored API key is bound to the host of the ``base_url`` it was saved for
   (``api_key_host`` in ``settings.json``).  It is only ever attached to
   requests whose host matches, so repointing ``base_url`` cannot carry it off
   the box.  ``cloud.comfy.org`` is the default binding when no host is known.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
import sys
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from fastapi import HTTPException, Request

from .comfy import url_host

DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173", "https://aitherium.github.io")


def normalize_origin(value: str) -> str:
    """``https://Example.com:443/`` -> ``https://example.com`` (compare-able)."""
    return str(value or "").strip().rstrip("/").lower()


def is_loopback_host(host: Optional[str]) -> bool:
    """True for 127.0.0.0/8, ::1, ``localhost`` and the in-process test client."""
    text = (host or "").strip().strip("[]").lower()
    if not text:
        return False
    if text in ("localhost", "testclient"):
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def origin_allowed(origin: Optional[str], allowed: Sequence[str] = ()) -> bool:
    """No Origin is fine (non-browser tooling); a browser Origin must be ours."""
    if origin is None:
        return True
    value = normalize_origin(origin)
    if not value or value == "null":
        return False
    if is_loopback_host(url_host(value)):
        return True
    return value in {normalize_origin(o) for o in allowed}


def _split_origins(raw: Optional[str]) -> Sequence[str]:
    if not raw:
        return ()
    return tuple(part.strip() for part in str(raw).split(",") if part.strip())


def cors_origins() -> Sequence[str]:
    """The dev UI plus anything the operator allowlisted via CINEMA_CORS_ORIGINS."""
    seen = []
    for origin in tuple(DEV_ORIGINS) + tuple(_split_origins(os.environ.get("CINEMA_CORS_ORIGINS"))):
        norm = normalize_origin(origin)
        if norm and norm not in seen:
            seen.append(norm)
    return tuple(seen)


def bind_host_from_argv(argv: Optional[Iterable[str]] = None) -> Optional[str]:
    """Read ``--host`` from the process arguments (``python -m uvicorn ...``)."""
    args = list(sys.argv[1:] if argv is None else argv)
    for index, arg in enumerate(args):
        if arg == "--host" and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("--host="):
            return arg.split("=", 1)[1]
    return None


def resolve_bind_host() -> Optional[str]:
    """CINEMA_HOST, else the uvicorn ``--host`` argument, else unknown."""
    return (os.environ.get("CINEMA_HOST") or "").strip() or bind_host_from_argv()


@dataclass
class SettingsAuth:
    """Who may write settings, and with what token."""

    token: Optional[str] = None
    generated: bool = False
    bind_host: Optional[str] = None
    allowed_origins: Sequence[str] = field(default_factory=tuple)

    @classmethod
    def from_env(
        cls,
        bind_host: Optional[str] = None,
        token: Optional[str] = None,
        allowed_origins: Optional[Sequence[str]] = None,
    ) -> "SettingsAuth":
        host = bind_host if bind_host is not None else resolve_bind_host()
        chosen = (token or os.environ.get("CINEMA_TOKEN") or "").strip() or None
        generated = False
        if not chosen and host and not is_loopback_host(host):
            # Bound to a network address with no token: mint one for this process
            # and say it out loud exactly once, or every remote write is refused.
            chosen = secrets.token_urlsafe(32)
            generated = True
            print(
                "[cinema] bound to %s with no CINEMA_TOKEN: generated one for this "
                "process.\n  Authorization: Bearer %s\nSet CINEMA_TOKEN to pin it "
                "across restarts." % (host, chosen),
                file=sys.stderr,
            )
        origins = (
            tuple(allowed_origins)
            if allowed_origins is not None
            else _split_origins(os.environ.get("CINEMA_CORS_ORIGINS"))
        )
        return cls(token=chosen, generated=generated, bind_host=host, allowed_origins=origins)

    # -- decisions --------------------------------------------------------
    def origin_allowed(self, origin: Optional[str]) -> bool:
        return origin_allowed(origin, self.allowed_origins)

    def bearer_ok(self, authorization: Optional[str]) -> bool:
        if not self.token or not authorization:
            return False
        scheme, _, supplied = authorization.partition(" ")
        if scheme.strip().lower() != "bearer":
            return False
        supplied = supplied.strip()
        return bool(supplied) and secrets.compare_digest(supplied, self.token)

    def check(self, request: Request) -> None:
        """Raise 401/403 unless this request may write settings."""
        authorization = request.headers.get("authorization")
        if self.bearer_ok(authorization):
            return
        client_host = request.client.host if request.client else None
        if is_loopback_host(client_host):
            # A wrong token does not take away the loopback allowance: the
            # token is an extra door, not a replacement for the local one.
            if self.origin_allowed(request.headers.get("origin")):
                return
            raise HTTPException(
                status_code=403,
                detail="cross-origin settings write refused",
            )
        if authorization:
            raise HTTPException(status_code=403, detail="invalid bearer token")
        raise HTTPException(
            status_code=401,
            detail=(
                "settings write requires a bearer token from a non-loopback client "
                "(set CINEMA_TOKEN; the server prints the generated one on startup)"
            ),
        )
