"""The tool implementations, as plain async functions.

``server.py`` adds MCP descriptions and schemas on top.  Keeping the bodies here
means every behaviour is testable without a client, a transport or a GPU, and
the MCP layer stays a thin adapter.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .client import CinemaClient, CinemaError
from .prompt import compile_prompt, rig_summary, validate_rig

ENGINES = ("keyframe", "t2v", "i2v", "flf2v")
QUALITIES = ("draft", "final")


def _check_choice(value: str, allowed: tuple, label: str) -> str:
    if value not in allowed:
        raise CinemaError("unknown %s %r — valid ids: %s" % (label, value, ", ".join(allowed)))
    return value


def _beat_list(beats_per_shot: Any, shots: int) -> List[int]:
    """One positive beat count per shot: an int repeats, a list must match."""
    if isinstance(beats_per_shot, (list, tuple)):
        raw = list(beats_per_shot)
        if len(raw) != shots:
            raise CinemaError(
                "beats_per_shot needs one entry per shot (%d shots, %d beats)"
                % (shots, len(raw))
            )
    else:
        raw = [beats_per_shot] * shots
    beats: List[int] = []
    for value in raw:
        try:
            count = int(value)
        except (TypeError, ValueError) as exc:
            raise CinemaError(
                "beats_per_shot must be a positive integer, got %r" % (value,)
            ) from exc
        if count <= 0:
            raise CinemaError("beats_per_shot must be a positive integer")
        beats.append(count)
    return beats


async def _vocab(client: CinemaClient) -> Dict[str, Any]:
    vocab = await client.vocab()
    if not isinstance(vocab, dict) or not vocab.get("moves"):
        raise CinemaError(
            "the backend at %s returned no camera vocabulary — it is not a Cinema backend"
            % client.url
        )
    return vocab


async def list_camera_moves(client: CinemaClient) -> Dict[str, Any]:
    vocab = await _vocab(client)
    moves = [
        {"id": m["id"], "label": m.get("label", m["id"])} for m in vocab.get("moves", [])
    ]
    lenses = [
        {"id": m["id"], "label": m.get("label", m["id"])} for m in vocab.get("lenses", [])
    ]
    speeds = [
        {"id": m["id"], "label": m.get("label", m["id"])} for m in vocab.get("speeds", [])
    ]
    framings = [
        {"id": m["id"], "label": m.get("label", m["id"])} for m in vocab.get("framings", [])
    ]
    return {
        "ok": True,
        "moves": moves,
        "lenses": lenses,
        "speeds": speeds,
        "framings": framings,
        "defaults": {"move": "static", "lens": "50mm", "speed": "slow", "framing": "medium"},
        "usage": (
            "Pass ids (not labels) as camera_rig.move/lens/speed/framing. A rig with no "
            "fields renders on the defaults; every render compiles to explicit camera "
            "clauses in the prompt."
        ),
    }


async def plan_shot(
    client: CinemaClient,
    prompt: str,
    camera_rig: Optional[Mapping[str, Any]] = None,
    engine: str = "t2v",
    quality: str = "draft",
) -> Dict[str, Any]:
    vocab = await _vocab(client)
    rig = validate_rig(camera_rig, vocab)
    _check_choice(engine, ENGINES, "engine")
    _check_choice(quality, QUALITIES, "quality")
    compiled = compile_prompt(rig, prompt, vocab)
    return {
        "ok": True,
        "engine": engine,
        "quality": quality,
        "camera_rig": rig,
        "rig_summary": rig_summary(rig, vocab),
        "subject": (prompt or "").strip(),
        "compiled_prompt": compiled,
        "note": (
            "No render was spent. The backend recompiles this same text at submit time; "
            "call render_shot with the same engine and camera_rig to shoot it."
        ),
    }


async def render_shot(
    client: CinemaClient,
    engine: str,
    camera_rig: Optional[Mapping[str, Any]] = None,
    prompt: str = "",
    start_media_id: Optional[str] = None,
    end_media_id: Optional[str] = None,
    negative: Optional[str] = None,
    seed: Optional[int] = None,
    quality: str = "draft",
) -> Dict[str, Any]:
    vocab = await _vocab(client)
    rig = validate_rig(camera_rig, vocab)
    _check_choice(engine, ENGINES, "engine")
    _check_choice(quality, QUALITIES, "quality")
    body: Dict[str, Any] = {
        "mode": engine,
        "prompt": prompt or "",
        "camera": rig,
        "quality": quality,
    }
    if start_media_id:
        body["start_media_id"] = start_media_id
    if end_media_id:
        body["end_media_id"] = end_media_id
    if negative:
        body["negative"] = negative
    if seed is not None:
        body["seed"] = int(seed)
    if engine in ("i2v", "flf2v") and not start_media_id:
        raise CinemaError(
            "engine %r needs start_media_id — a reference still the shot is seeded from "
            "(upload one with POST /api/upload; the human supplies files)" % engine
        )
    if engine == "flf2v" and not end_media_id:
        raise CinemaError("engine 'flf2v' needs end_media_id as well as start_media_id")
    result = await client.create_shot(body)
    return {
        "ok": True,
        "shot_id": result.get("shot_id"),
        "job_id": result.get("job_id"),
        "engine": engine,
        "quality": quality,
        "camera_rig": rig,
        "compiled_prompt": compile_prompt(rig, prompt, vocab),
        "next": (
            "poll get_shot(%r) until status is 'done' — a draft film is minutes on a "
            "5090, a final is longer; do not re-submit while it runs" % result.get("shot_id")
        ),
    }


async def reshoot_shot(
    client: CinemaClient,
    source_media_id: str,
    camera_rig: Optional[Mapping[str, Any]] = None,
    at_s: Optional[float] = None,
    prompt: str = "",
    seed: Optional[int] = None,
    quality: str = "draft",
) -> Dict[str, Any]:
    vocab = await _vocab(client)
    rig = validate_rig(camera_rig, vocab)
    _check_choice(quality, QUALITIES, "quality")
    if not source_media_id:
        raise CinemaError("reshoot_shot needs source_media_id (a shot's media_id or an upload)")
    body: Dict[str, Any] = {
        "mode": "reshoot",
        "prompt": prompt or "",
        "camera": rig,
        "quality": quality,
        "source_media_id": source_media_id,
    }
    if at_s is not None:
        body["at_s"] = float(at_s)
    if seed is not None:
        body["seed"] = int(seed)
    result = await client.create_shot(body)
    return {
        "ok": True,
        "shot_id": result.get("shot_id"),
        "job_id": result.get("job_id"),
        "source_media_id": source_media_id,
        "at_s": at_s,
        "camera_rig": rig,
        "note": (
            "The frame is lifted from the source (video: at at_s, default the last frame; "
            "image: itself) and animated again with YOUR camera rig, so it is the same "
            "scene from a new angle."
        ),
    }


async def grade_shot(
    client: CinemaClient,
    shot_id: str,
    look: str,
    strength: float = 1.0,
) -> Dict[str, Any]:
    if not shot_id:
        raise CinemaError("grade_shot needs shot_id")
    if not (0.0 <= float(strength) <= 1.0):
        raise CinemaError("strength must be between 0 and 1 (0.8 is a natural film grade)")
    looks = await client.looks()
    valid = [str(item.get("id")) for item in looks] if isinstance(looks, list) else []
    if look not in valid:
        raise CinemaError("unknown look %r — valid looks: %s" % (look, ", ".join(valid)))
    result = await client.grade_shot(shot_id, look, float(strength))
    return {
        "ok": True,
        "shot_id": shot_id,
        "job_id": result.get("job_id"),
        "look": look,
        "strength": float(strength),
        "next": (
            "the graded frame/clip becomes shot.%s when the job finishes; get_shot reports "
            "graded_media_id" % "graded_media_id"
        ),
    }


async def list_shots(client: CinemaClient, limit: int = 50) -> Dict[str, Any]:
    shots = await client.list_shots()
    if not isinstance(shots, list):
        shots = []
    shots = shots[: max(1, int(limit))]
    rows = []
    for shot in shots:
        rows.append(
            {
                "shot_id": shot.get("shot_id"),
                "mode": shot.get("mode"),
                "status": shot.get("status"),
                "progress": shot.get("progress"),
                "prompt": (shot.get("prompt") or "")[:120],
                "camera": shot.get("camera"),
                "media_url": client.media_url(shot["media_id"]) if shot.get("media_id") else None,
                "graded_media_url": (
                    client.media_url(shot["graded_media_id"])
                    if shot.get("graded_media_id")
                    else None
                ),
            }
        )
    return {"ok": True, "count": len(rows), "shots": rows}


async def get_shot(client: CinemaClient, shot_id: str) -> Dict[str, Any]:
    if not shot_id:
        raise CinemaError("get_shot needs shot_id")
    shot = await client.get_shot(shot_id)
    out = dict(shot)
    if shot.get("media_id"):
        out["media_url"] = client.media_url(shot["media_id"])
    if shot.get("graded_media_id"):
        out["graded_media_url"] = client.media_url(shot["graded_media_id"])
    out["ok"] = True
    return out


async def cut_sequence(client: CinemaClient, shot_ids: List[str]) -> Dict[str, Any]:
    ids = [str(s).strip() for s in (shot_ids or []) if str(s).strip()]
    if not ids:
        raise CinemaError("cut_sequence needs a non-empty shot_ids list, in screen order")
    result = await client.create_sequence(ids)
    return {
        "ok": True,
        "job_id": result.get("job_id"),
        "shot_ids": ids,
        "note": (
            "shots are concatenated in the order given, each graded clip used when the shot "
            "has one; poll the job at GET /api/jobs/{job_id} for the sequence media_id"
        ),
    }


async def cut_music_video(
    client: CinemaClient,
    shot_ids: List[str],
    bpm: float = 120.0,
    beats_per_shot: Any = 4,
    track_duration_s: Optional[float] = None,
    audio_media_id: Optional[str] = None,
    fit: Optional[str] = None,
) -> Dict[str, Any]:
    """Cut a sequence on a beat sheet: the order is the edit, the beat grid the clock."""
    ids = [str(s).strip() for s in (shot_ids or []) if str(s).strip()]
    if not ids:
        raise CinemaError("cut_music_video needs a non-empty shot_ids list, in song order")
    try:
        tempo = float(bpm)
    except (TypeError, ValueError) as exc:
        raise CinemaError("bpm must be a number, got %r" % (bpm,)) from exc
    if tempo <= 0:
        raise CinemaError("bpm must be positive, got %r" % (bpm,))
    beats = _beat_list(beats_per_shot, len(ids))

    beat_s = 60.0 / tempo
    sheet = []
    cursor = 0.0
    for i, (sid, count) in enumerate(zip(ids, beats)):
        span = round(count * beat_s, 3)
        sheet.append(
            {
                "order": i + 1,
                "shot_id": sid,
                "start_s": round(cursor, 3),
                "end_s": round(cursor + span, 3),
                "beats": count,
            }
        )
        cursor += span
    plan_duration = round(cursor, 3)

    # The backend retimes and muxes in one pass, so send the actual sheet —
    # not only the ids — or the cut comes back on the clips' native timing.
    audio_media_id = (str(audio_media_id).strip() if audio_media_id else "") or None
    result = await client.create_sequence(
        ids,
        audio_media_id=audio_media_id,
        bpm=tempo,
        beats_per_shot=beats,
        fit=fit,
    )
    plan: Dict[str, Any] = {
        "ok": True,
        "job_id": result.get("job_id"),
        "bpm": tempo,
        "beats_per_shot": beats,
        "per_shot_s": round(beats[0] * beat_s, 3) if len(set(beats)) == 1 else None,
        "plan_duration_s": plan_duration,
        "cut_sheet": sheet,
        "shot_ids": ids,
        "audio_media_id": audio_media_id,
        "music_video": bool(audio_media_id),
    }
    if track_duration_s is not None:
        track = float(track_duration_s)
        plan["track_duration_s"] = track
        plan["track_fit"] = "fits" if cursor <= track else "overruns"
        plan["tail_s"] = round(max(0.0, track - cursor), 3)
        if cursor > track:
            plan["advice"] = (
                "the plan overruns the track by %.3fs — lower beats_per_shot to %d or drop a "
                "shot" % (cursor - track, max(1, int(track / beat_s)))
            )
    if audio_media_id:
        plan["note"] = (
            "The backend cuts the shots to the beat sheet with hard cuts (each shot setpts-"
            "retimed to beats_per_shot[i] * 60/bpm, never a frozen tail) and muxes the audio "
            "as AAC; poll the job at GET /api/jobs/{job_id} for the sequence media_id."
        )
        if (fit or "").lower() == "audio":
            plan["note"] += " 'fit': 'audio' stretched the last shot so the picture ends with the track."
    else:
        plan["note"] = (
            "No audio_media_id was passed, so the cut is retimed to the beat sheet (hard cuts, "
            "setpts) but silent. Pass audio_media_id from an audio upload to mux the track."
        )
    return plan


async def backend_status(client: CinemaClient) -> Dict[str, Any]:
    health = await client.health()
    shots = await client.list_shots()
    jobs = await client.list_jobs()
    running = [
        s for s in (shots or []) if s.get("status") in ("queued", "running")
    ]
    return {
        "ok": True,
        "backend": client.url,
        "version": health.get("version"),
        "comfy": health.get("comfy"),
        "grade": health.get("grade"),
        "shots": {"total": len(shots or []), "in_flight": len(running)},
        "jobs": {"total": len(jobs or [])},
        "ready_to_render": bool((health.get("comfy") or {}).get("reachable")),
        "note": (
            "ready_to_render is false when ComfyUI is not reachable at the backend's "
            "COMFY_BASE_URL; shots will queue and fail, so start ComfyUI first."
            if not (health.get("comfy") or {}).get("reachable")
            else "ComfyUI is reachable; render_shot will queue immediately."
        ),
    }
