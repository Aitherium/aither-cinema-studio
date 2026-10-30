"""HTTP client for the Aither Cinema Studio backend.

The MCP server owns no production state: every tool is a thin, agent-shaped
wrapper around the backend's JSON API (``server/cinema/app.py``).  ``CINEMA_URL``
selects the backend and defaults to the port the README quickstart uses, so a
default install with the server running on :8765 needs no configuration.

Errors are raised as :class:`CinemaError` with a message an agent can act on:
the backend's own ``detail`` when it rejected the request, or the exact command
that starts a backend that is not answering.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

DEFAULT_CINEMA_URL = "http://127.0.0.1:8765"
DEFAULT_TIMEOUT_S = 120.0

START_HINT = (
    "start it with: cd server && .venv/Scripts/python -m uvicorn cinema.app:app "
    "--port 8765   (.venv/bin/python on Linux/macOS)"
)


class CinemaError(RuntimeError):
    """A backend call failed; the message is written for an agent to act on."""


def base_url() -> str:
    """The backend URL from ``CINEMA_URL`` (default ``http://127.0.0.1:8765``)."""
    return (os.environ.get("CINEMA_URL") or DEFAULT_CINEMA_URL).rstrip("/")


def _timeout_default() -> float:
    raw = os.environ.get("CINEMA_TIMEOUT_S")
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S


def _detail(resp: httpx.Response) -> str:
    """The backend's ``detail`` field, or a clipped body."""
    try:
        payload = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:300] or resp.reason_phrase
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
    return str(payload)[:300]


class CinemaClient:
    """One JSON call per method against the Cinema backend."""

    def __init__(self, url: Optional[str] = None, timeout_s: Optional[float] = None) -> None:
        self.url = (url or base_url()).rstrip("/")
        self.timeout_s = float(timeout_s if timeout_s is not None else _timeout_default())

    # -- transport --------------------------------------------------------
    async def _request(
        self, method: str, path: str, json: Optional[Dict[str, Any]] = None
    ) -> Any:
        timeout = httpx.Timeout(self.timeout_s, connect=5.0)
        try:
            async with httpx.AsyncClient(
                base_url=self.url, timeout=timeout, follow_redirects=False
            ) as client:
                resp = await client.request(method, path, json=json)
        except httpx.ConnectError as exc:
            raise CinemaError(
                "cannot reach the Cinema backend at %s — %s, or set CINEMA_URL"
                % (self.url, START_HINT)
            ) from exc
        except httpx.TimeoutException as exc:
            raise CinemaError(
                "the Cinema backend at %s did not answer within %.0fs for %s %s — it is "
                "busy queueing a render; poll get_shot instead of re-submitting"
                % (self.url, self.timeout_s, method, path)
            ) from exc

        if resp.status_code >= 400:
            raise CinemaError(
                "%s %s -> %d: %s" % (method, path, resp.status_code, _detail(resp))
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # -- read -------------------------------------------------------------
    async def health(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/health")

    async def vocab(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/camera/vocab")

    async def looks(self) -> List[Dict[str, str]]:
        return await self._request("GET", "/api/grade/looks")

    async def list_shots(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/shots")

    async def get_shot(self, shot_id: str) -> Dict[str, Any]:
        return await self._request("GET", "/api/shots/%s" % shot_id)

    async def get_job(self, job_id: str) -> Dict[str, Any]:
        return await self._request("GET", "/api/jobs/%s" % job_id)

    async def list_jobs(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/jobs")

    def media_url(self, media_id: str) -> str:
        """Absolute URL an agent or human can open for a media id."""
        return "%s/api/media/%s" % (self.url, media_id)

    # -- write ------------------------------------------------------------
    async def create_shot(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("POST", "/api/shots", json=body)

    async def grade_shot(self, shot_id: str, look: str, strength: float) -> Dict[str, Any]:
        return await self._request(
            "POST", "/api/shots/%s/grade" % shot_id, json={"look": look, "strength": strength}
        )

    async def create_sequence(
        self,
        shot_ids: List[str],
        audio_media_id: Optional[str] = None,
        bpm: Optional[float] = None,
        beats_per_shot: Optional[List[int]] = None,
        fit: Optional[str] = None,
    ) -> Dict[str, Any]:
        """``POST /api/sequence`` — a plain cut, or a beat cut with an audio bed.

        The music fields are omitted from the body unless given, so an older
        backend that only knows ``shot_ids`` still receives exactly that.
        """
        body: Dict[str, Any] = {"shot_ids": list(shot_ids)}
        if audio_media_id:
            body["audio_media_id"] = audio_media_id
        if bpm is not None:
            body["bpm"] = float(bpm)
        if beats_per_shot is not None:
            body["beats_per_shot"] = [int(b) for b in beats_per_shot]
        if fit:
            body["fit"] = fit
        return await self._request("POST", "/api/sequence", json=body)
