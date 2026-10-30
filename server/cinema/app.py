"""FastAPI application: the HTTP surface of Aither Cinema Studio.

Routes (contract):

  GET  /api/health
  POST /api/upload                      multipart `file`
  GET  /api/media                       list
  GET  /api/media/{media_id}            file bytes
  POST /api/shots                       create + render one shot
  GET  /api/shots                       list
  GET  /api/shots/{shot_id}             one shot (status, progress, graph)
  POST /api/shots/{shot_id}/grade       film grade -> new job + media
  POST /api/sequence                    cut shots together -> new job + media
  GET  /api/camera/vocab                the one camera table

Plus ``GET /api/jobs`` / ``GET /api/jobs/{job_id}`` (progress for grade and
sequence jobs, which the shot record cannot carry) and ``GET /api/grade/looks``.

``web/dist`` is served at ``/`` when it exists (vite dev runs on :5173 and
talks to this origin through CORS).
"""

from __future__ import annotations

import json
import mimetypes
import os
import random
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__, camera, grade, graphs
from .comfy import ComfyClient, ComfyError, Settings, save_stored_settings
from .jobs import JobStore, JsonStore
from .media import MediaStore, new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROBE_FILE = Path(os.environ.get("CINEMA_PROBE_FILE") or (REPO_ROOT / "docs" / "comfy-probe.json"))
PROBE_TTL_S = 300.0
PROBE_FAIL_TTL_S = 30.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


@dataclass
class CinemaState:
    data_dir: Path
    media: MediaStore
    shots: JsonStore
    jobs: JobStore
    comfy: Any
    settings: Settings = field(default_factory=Settings.from_env)
    probe_file: Path = DEFAULT_PROBE_FILE
    job_timeout: float = field(default_factory=lambda: _env_float("CINEMA_JOB_TIMEOUT_S", 3600.0))
    _probe: Optional[Dict[str, Any]] = None
    _probe_at: float = 0.0

    # -- probe cache ------------------------------------------------------
    def get_probe(self, force: bool = False) -> Dict[str, Any]:
        """Node registry facts: live /object_info, else docs/comfy-probe.json.

        The probe decides the video output node, so it is cached for 5 min and
        a failure is cached for 30 s so the UI is not slowed by a dead server.
        An empty dict means "unknown" - the builders then assume a current core
        ComfyUI (SaveVideo + CreateVideo).
        """
        now = time.monotonic()
        if not force and self._probe is not None:
            ttl = PROBE_TTL_S if self._probe else PROBE_FAIL_TTL_S
            if now - self._probe_at < ttl:
                return self._probe
        probe: Dict[str, Any] = {}
        try:
            if self.probe_file and Path(self.probe_file).is_file():
                with open(self.probe_file, "r", encoding="utf-8") as fh:
                    doc = json.load(fh)
                if isinstance(doc, dict) and doc:
                    probe = doc
        except (OSError, ValueError):
            probe = {}
        if not probe:
            try:
                probe = self.comfy.object_info()
            except Exception:  # noqa: BLE001 - a dead server is a normal state
                probe = {}
        self._probe = probe if isinstance(probe, dict) else {}
        self._probe_at = now
        return self._probe

    def tmpdir(self) -> Path:
        d = self.data_dir / "tmp"
        d.mkdir(parents=True, exist_ok=True)
        return d


def _surface(state: "CinemaState") -> str:
    return str(getattr(state.comfy, "surface", "local") or "local")


def build_state(data_dir: Optional[Path] = None, inline: bool = False, comfy: Any = None) -> CinemaState:
    data = Path(data_dir or os.environ.get("CINEMA_DATA_DIR") or "./data").resolve()
    data.mkdir(parents=True, exist_ok=True)
    settings = Settings.resolve(data_dir=data)
    return CinemaState(
        data_dir=data,
        media=MediaStore(data),
        shots=JsonStore(data / "shots.json"),
        jobs=JobStore(data, inline=inline),
        comfy=comfy if comfy is not None else ComfyClient(data_dir=data),
        settings=settings,
    )


# ---------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------
class ShotRequest(BaseModel):
    mode: str
    prompt: str = ""
    negative: Optional[str] = None
    camera: Optional[Dict[str, Any]] = None
    start_media_id: Optional[str] = None
    end_media_id: Optional[str] = None
    source_media_id: Optional[str] = None
    seed: Optional[int] = None
    quality: str = "draft"
    at_s: Optional[float] = None
    engine: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    frames: Optional[int] = None
    steps: Optional[int] = None


class GradeRequest(BaseModel):
    look: str = "none"
    strength: float = 1.0


class SequenceRequest(BaseModel):
    shot_ids: List[str]
    # music-video cut: each shot holds beats_per_shot[i] * 60/bpm seconds
    audio_media_id: Optional[str] = None
    bpm: Optional[float] = None
    beats_per_shot: Optional[List[int]] = None
    # "audio" stretches the last shot so the picture ends with the track
    fit: Optional[str] = None


class SettingsPatch(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None


def _reload_comfy(state: CinemaState) -> None:
    """Re-resolve settings after a PUT; rebuild the client when it is a real one."""
    state.settings = Settings.resolve(data_dir=state.data_dir)
    client = getattr(state, "comfy", None)
    if isinstance(client, ComfyClient):
        client.close()
        state.comfy = ComfyClient(data_dir=state.data_dir)


# ---------------------------------------------------------------------------
# shot execution
# ---------------------------------------------------------------------------
def _shot_runner(state: CinemaState, shot_id: str):
    def runner(job_id: str, _job: Dict[str, Any]) -> Dict[str, Any]:
        shot = state.shots.get(shot_id)
        if shot is None:
            raise RuntimeError("shot %s disappeared" % shot_id)
        t0 = time.monotonic()
        state.shots.update(shot_id, status="running", progress=0.05, job_id=job_id)
        state.jobs.progress(job_id, 0.05)

        mode = shot["mode"]
        probe = state.get_probe()
        mdl = graphs.models(shot.get("models_override"), surface=_surface(state))
        graph = shot.get("graph")
        rig = shot.get("camera") or {}

        if mode in ("i2v", "reshoot", "flf2v"):
            if graph is None:
                start_name, end_name = _upload_references(state, shot, mode)
                graph = graphs.build_shot(
                    mode=mode,
                    prompt=shot["compiled_prompt"],
                    negative=shot.get("negative") or "",
                    quality=shot["quality"],
                    seed=shot["seed"],
                    image_name=start_name,
                    end_image_name=end_name,
                    mdl=mdl,
                    probe=probe,
                    prefix="cinema/%s/%s" % (mode, shot_id),
                    engine=shot.get("engine"),
                    move=str(rig.get("move") or "static"),
                    speed=graphs.SPEED_VALUES.get(str(rig.get("speed") or "slow"), 1.0),
                    width=shot.get("width"),
                    height=shot.get("height"),
                    frames=shot.get("frames"),
                    steps=shot.get("steps"),
                )
                state.shots.update(shot_id, graph=graph)

        if graph is None:
            raise RuntimeError("no graph for shot %s" % shot_id)

        def on_status(status: str) -> None:
            if status == "submitted":
                state.shots.update(shot_id, progress=0.15)
                state.jobs.progress(job_id, 0.15)
            elif status == "running":
                state.shots.update(shot_id, progress=0.4)
                state.jobs.progress(job_id, 0.4)

        result = state.comfy.run(graph, timeout=state.job_timeout, on_status=on_status)
        outputs = result.get("outputs") or []
        if not outputs:
            raise ComfyError("ComfyUI finished but produced no output file")
        # A motion shot prefers a real video over a still preview.
        if mode != "keyframe":
            outputs = sorted(outputs, key=lambda o: 0 if o.get("kind") == "video" else 1)
        chosen = outputs[0]
        data = state.comfy.download(chosen)
        ext = Path(chosen.get("filename") or "").suffix or (
            ".mp4" if mode != "keyframe" else ".png"
        )
        rec = state.media.add_bytes(data, ext, origin="render", name=chosen.get("filename"))
        state.shots.update(
            shot_id,
            status="done",
            progress=1.0,
            media_id=rec["media_id"],
            error=None,
            finished=now_iso(),
            elapsed_s=round(time.monotonic() - t0, 1),
        )
        return {
            "media_id": rec["media_id"],
            "kind": rec["kind"],
            "prompt_id": result.get("prompt_id"),
            "shot_id": shot_id,
        }

    return runner


def _upload_references(state: CinemaState, shot: Dict[str, Any], mode: str):
    """Upload the reference image(s) and return the LoadImage names."""
    tmp = Path(tempfile.mkdtemp(prefix="cinema-ref-", dir=str(state.tmpdir())))
    try:
        start_id = shot.get("source_media_id") if mode == "reshoot" else shot.get("start_media_id")
        if not start_id:
            start_id = shot.get("source_media_id")
        src = state.media.path(start_id) if start_id else None
        if src is None:
            raise RuntimeError("reference media %r not found" % (start_id,))
        if mode == "reshoot" and shot.get("mode") == "reshoot" and _is_video(src):
            frame = tmp / "frame.png"
            _frame_or_copy(src, frame, shot.get("at_s"))
            src = frame
        start_name = state.comfy.upload_image(
            Path(src).read_bytes(), "cinema_%s_start.png" % shot["shot_id"]
        )
        end_name = None
        if mode == "flf2v":
            end_src = state.media.path(shot.get("end_media_id") or "")
            if end_src is None:
                raise RuntimeError("end media %r not found" % (shot.get("end_media_id"),))
            end_name = state.comfy.upload_image(
                Path(end_src).read_bytes(), "cinema_%s_end.png" % shot["shot_id"]
            )
        return start_name, end_name
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _is_video(path: Path) -> bool:
    from . import ffmpeg

    return ffmpeg.is_video(Path(path))


def _frame_or_copy(src: Path, dst: Path, at_s: Optional[float]) -> None:
    from . import ffmpeg

    if ffmpeg.is_video(src):
        ffmpeg.frame_at(src, dst, at_s)
    else:
        shutil.copyfile(src, dst)


def _sequence_runner(
    state: CinemaState,
    shot_ids: List[str],
    audio_media_id: Optional[str] = None,
    bpm: Optional[float] = None,
    beats_per_shot: Optional[List[int]] = None,
    fit: Optional[str] = None,
):
    def runner(job_id: str, _job: Dict[str, Any]) -> Dict[str, Any]:
        from . import ffmpeg

        paths: List[Path] = []
        for sid in shot_ids:
            shot = state.shots.get(sid)
            if shot is None:
                raise RuntimeError("shot %s not found" % sid)
            mid = shot.get("graded_media_id") or shot.get("media_id")
            if not mid:
                raise RuntimeError("shot %s has no rendered media yet" % sid)
            p = state.media.path(mid)
            if p is None:
                raise RuntimeError("media %s for shot %s is missing" % (mid, sid))
            paths.append(p)

        audio_path = None
        if audio_media_id:
            audio_path = state.media.path(audio_media_id)
            if audio_path is None:
                raise RuntimeError("audio media %s is missing" % audio_media_id)

        durations = None
        if beats_per_shot and bpm:
            tempo = float(bpm)
            durations = [max(1, int(b)) * 60.0 / tempo for b in beats_per_shot]

        state.jobs.progress(job_id, 0.2)
        out = state.tmpdir() / ("sequence_%s.mp4" % job_id)
        total_s: Optional[float] = None
        if durations is None and audio_path is None:
            ffmpeg.concat(paths, out)
        else:
            total_s = ffmpeg.music_cut(
                paths,
                out,
                durations=durations,
                audio=audio_path,
                fit_audio=(fit or "").lower() == "audio",
            )
        state.jobs.progress(job_id, 0.9)
        rec = state.media.add_file(out, origin="sequence", ext=".mp4")
        out.unlink(missing_ok=True)
        result: Dict[str, Any] = {"media_id": rec["media_id"], "shot_ids": shot_ids}
        if total_s is not None:
            result["duration_s"] = round(total_s, 3)
        if audio_path is not None:
            result["audio_media_id"] = audio_media_id
            result["music_video"] = True
        if bpm is not None:
            result["bpm"] = float(bpm)
        if beats_per_shot is not None:
            result["beats_per_shot"] = [int(b) for b in beats_per_shot]
        return result

    return runner


def _grade_runner(state: CinemaState, shot_id: str, look: str, strength: float):
    def runner(job_id: str, _job: Dict[str, Any]) -> Dict[str, Any]:
        shot = state.shots.get(shot_id)
        if shot is None:
            raise RuntimeError("shot %s not found" % shot_id)
        mid = shot.get("media_id")
        src = state.media.path(mid) if mid else None
        if src is None:
            raise RuntimeError("shot %s has no rendered media yet" % shot_id)
        out = state.tmpdir() / ("grade_%s%s" % (job_id, Path(src).suffix or ".png"))
        grade.grade(src, out, look=look, strength=strength)
        state.jobs.progress(job_id, 0.9)
        rec = state.media.add_file(out, origin="grade", ext=Path(out).suffix)
        out.unlink(missing_ok=True)
        state.shots.update(shot_id, graded_media_id=rec["media_id"])
        return {"media_id": rec["media_id"], "shot_id": shot_id, "look": look, "strength": strength}

    return runner


# ---------------------------------------------------------------------------
# submit a shot (shared by the route and the CLI, so both take one code path)
# ---------------------------------------------------------------------------
class RequestError(ValueError):
    """A bad shot request (the route turns this into HTTP 400)."""


def submit_shot(state: CinemaState, body: ShotRequest) -> Dict[str, str]:
    if body.mode not in graphs.MODES:
        raise RequestError(
            "unknown mode %r (choose one of: %s)" % (body.mode, ", ".join(graphs.MODES))
        )
    try:
        rig = camera.validate_rig(body.camera)
    except camera.CameraError as exc:
        raise RequestError(str(exc)) from exc
    prompt = (body.prompt or "").strip()
    mode = body.mode
    if not prompt and mode != "reshoot":
        raise RequestError("prompt is required")
    if body.quality not in graphs.PRESETS:
        raise RequestError(
            "unknown quality %r (choose one of: %s)"
            % (body.quality, ", ".join(graphs.PRESETS))
        )

    # reference media must exist before the shot is accepted
    if mode == "i2v" and not body.start_media_id:
        raise RequestError("i2v needs start_media_id")
    if mode == "flf2v" and not (body.start_media_id and body.end_media_id):
        raise RequestError("flf2v needs start_media_id and end_media_id")
    if mode == "reshoot" and not (body.source_media_id or body.start_media_id):
        raise RequestError("reshoot needs source_media_id")
    for field_name, mid in (
        ("start_media_id", body.start_media_id),
        ("end_media_id", body.end_media_id),
        ("source_media_id", body.source_media_id),
    ):
        if mid and state.media.get(mid) is None:
            raise RequestError("%s %r not found" % (field_name, mid))

    engine = body.engine or graphs.default_engine(mode)
    if engine not in graphs.ENGINES:
        raise RequestError(
            "unknown engine %r (choose one of: %s)" % (engine, ", ".join(graphs.ENGINES))
        )

    seed = body.seed if body.seed is not None else random.randint(0, 2**31 - 1)
    compiled = camera.compile(rig, prompt)
    shot_id = new_id("s")
    graph = None
    if mode in ("keyframe", "t2v"):
        try:
            graph = graphs.build_shot(
                mode=mode,
                prompt=compiled,
                negative=body.negative or "",
                quality=body.quality,
                seed=int(seed),
                mdl=graphs.models(surface=_surface(state)),
                probe=state.get_probe(),
                prefix="cinema/%s/%s" % (mode, shot_id),
                engine=engine,
                move=str(rig.get("move") or "static"),
                speed=graphs.SPEED_VALUES.get(str(rig.get("speed") or "slow"), 1.0),
                width=body.width,
                height=body.height,
                frames=body.frames,
                steps=body.steps,
            )
        except graphs.GraphError as exc:
            raise RequestError(str(exc)) from exc

    record = {
        "shot_id": shot_id,
        "mode": mode,
        "prompt": prompt,
        "negative": body.negative or "",
        "camera": rig,
        "quality": body.quality,
        "seed": int(seed),
        "start_media_id": body.start_media_id,
        "end_media_id": body.end_media_id,
        "source_media_id": body.source_media_id
        or (body.start_media_id if mode == "reshoot" else None),
        "at_s": body.at_s,
        "engine": engine,
        "width": body.width,
        "height": body.height,
        "frames": body.frames,
        "steps": body.steps,
        "status": "queued",
        "progress": 0.0,
        "media_id": None,
        "graded_media_id": None,
        "error": None,
        "compiled_prompt": compiled,
        "graph": graph,
        "created": now_iso(),
    }
    state.shots.put(shot_id, record)
    job = state.jobs.create(
        "shot",
        _shot_runner(state, shot_id),
        params={"shot_id": shot_id, "mode": mode},
    )
    state.shots.update(shot_id, job_id=job["job_id"])
    return {"shot_id": shot_id, "job_id": job["job_id"]}


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------
def create_app(state: Optional[CinemaState] = None) -> FastAPI:
    st = state or build_state()
    app = FastAPI(title="Aither Cinema Studio", version=__version__)
    app.state.cinema = st

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    state_ref = st

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        reachable = False
        version = None
        try:
            reachable = bool(state_ref.comfy.reachable())
            if reachable:
                version = state_ref.comfy.version()
        except Exception:  # noqa: BLE001
            reachable = False
        grade_status = grade.status()
        return {
            "ok": True,
            "version": __version__,
            "comfy": {
                "reachable": reachable,
                "base": getattr(state_ref.comfy, "base_url", None) or state_ref.settings.base_url,
                "version": version,
            },
            "grade": grade_status,
        }

    # -- settings ---------------------------------------------------------
    @app.get("/api/settings")
    def get_settings() -> Dict[str, Any]:
        return state_ref.settings.public()

    @app.put("/api/settings")
    def put_settings(body: SettingsPatch) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}
        if body.base_url is not None:
            base = body.base_url.strip().rstrip("/")
            if base and not (base.startswith("http://") or base.startswith("https://")):
                raise HTTPException(
                    status_code=400, detail="base_url must start with http:// or https://"
                )
            fields["base_url"] = base or None
        if body.api_key is not None:
            fields["api_key"] = body.api_key.strip() or None
        save_stored_settings(state_ref.data_dir, **fields)
        _reload_comfy(state_ref)
        return state_ref.settings.public()

    @app.post("/api/settings/test")
    def test_settings() -> Dict[str, Any]:
        client = state_ref.comfy
        if not hasattr(client, "test_credentials"):
            client = ComfyClient(data_dir=state_ref.data_dir)
        return client.test_credentials()

    # -- media ------------------------------------------------------------
    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)) -> Dict[str, Any]:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="empty upload")
        name = file.filename or "upload"
        ext = Path(name).suffix.lower()
        if not ext:
            ctype = (file.content_type or "").lower()
            if ctype.startswith("video/"):
                ext = ".mp4"
            elif ctype.startswith("image/"):
                ext = ".png"
            else:
                ext = ".bin"
        rec = state_ref.media.add_bytes(data, ext, origin="upload", name=name)
        return {"media_id": rec["media_id"], "kind": rec["kind"], "url": state_ref.media.url(rec["media_id"])}

    @app.get("/api/media")
    def list_media() -> List[Dict[str, Any]]:
        return [{**r, "url": state_ref.media.url(r["media_id"])} for r in state_ref.media.list()]

    @app.get("/api/media/{media_id}")
    def get_media(media_id: str):
        rec = state_ref.media.get(media_id)
        path = state_ref.media.path(media_id)
        if rec is None or path is None:
            raise HTTPException(status_code=404, detail="media not found")
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return FileResponse(str(path), media_type=ctype, filename=rec.get("filename") or path.name)

    # -- camera -----------------------------------------------------------
    @app.get("/api/camera/vocab")
    def camera_vocab() -> Dict[str, Any]:
        return camera.vocab()

    @app.get("/api/grade/looks")
    def grade_looks() -> List[Dict[str, str]]:
        return grade.looks_table()

    # -- shots ------------------------------------------------------------
    @app.post("/api/shots")
    def create_shot(body: ShotRequest) -> Dict[str, Any]:
        try:
            return submit_shot(state_ref, body)
        except RequestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/shots")
    def list_shots() -> List[Dict[str, Any]]:
        return state_ref.shots.all()

    @app.get("/api/shots/{shot_id}")
    def get_shot(shot_id: str) -> Dict[str, Any]:
        shot = state_ref.shots.get(shot_id)
        if shot is None:
            raise HTTPException(status_code=404, detail="shot not found")
        return shot

    @app.post("/api/shots/{shot_id}/grade")
    def grade_shot(shot_id: str, body: GradeRequest) -> Dict[str, Any]:
        shot = state_ref.shots.get(shot_id)
        if shot is None:
            raise HTTPException(status_code=404, detail="shot not found")
        try:
            look = grade.validate_look(body.look)
        except grade.GradeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not (0.0 <= float(body.strength) <= 1.0):
            raise HTTPException(status_code=400, detail="strength must be between 0 and 1")
        if not shot.get("media_id"):
            raise HTTPException(status_code=409, detail="shot has no rendered media yet")
        job = state_ref.jobs.create(
            "grade",
            _grade_runner(state_ref, shot_id, look, float(body.strength)),
            params={"shot_id": shot_id, "look": look, "strength": float(body.strength)},
        )
        return {"job_id": job["job_id"], "shot_id": shot_id}

    # -- sequence ---------------------------------------------------------
    @app.post("/api/sequence")
    def create_sequence(body: SequenceRequest) -> Dict[str, Any]:
        ids = list(body.shot_ids)
        if not ids:
            raise HTTPException(status_code=400, detail="shot_ids must not be empty")
        for sid in ids:
            if state_ref.shots.get(sid) is None:
                raise HTTPException(status_code=400, detail="shot %s not found" % sid)

        audio_media_id = (body.audio_media_id or "").strip() or None
        if audio_media_id and state_ref.media.path(audio_media_id) is None:
            raise HTTPException(
                status_code=400, detail="audio_media_id %s not found" % audio_media_id
            )
        bpm = float(body.bpm) if body.bpm is not None else None
        if bpm is not None and bpm <= 0:
            raise HTTPException(status_code=400, detail="bpm must be positive")
        beats = body.beats_per_shot
        if beats is not None:
            beats = [int(b) for b in beats]
            if len(beats) != len(ids):
                raise HTTPException(
                    status_code=400,
                    detail="beats_per_shot needs one entry per shot (%d shots, %d entries)"
                    % (len(ids), len(beats)),
                )
            if any(b <= 0 for b in beats):
                raise HTTPException(status_code=400, detail="beats_per_shot entries must be positive")
            if bpm is None:
                raise HTTPException(
                    status_code=400, detail="bpm is required when beats_per_shot is given"
                )
        fit = (body.fit or "").strip().lower() or None
        if fit not in (None, "audio", "video"):
            raise HTTPException(status_code=400, detail="fit must be 'audio' or 'video'")

        params: Dict[str, Any] = {"shot_ids": ids}
        if audio_media_id:
            params["audio_media_id"] = audio_media_id
        if bpm is not None:
            params["bpm"] = bpm
        if beats is not None:
            params["beats_per_shot"] = beats
        if fit is not None:
            params["fit"] = fit

        job = state_ref.jobs.create(
            "sequence",
            _sequence_runner(state_ref, ids, audio_media_id, bpm, beats, fit),
            params=params,
        )
        echoed = {k: v for k, v in params.items() if k != "shot_ids"}
        return {"job_id": job["job_id"], **echoed, "music_video": bool(audio_media_id)}

    # -- jobs -------------------------------------------------------------
    @app.get("/api/jobs")
    def list_jobs() -> List[Dict[str, Any]]:
        return state_ref.jobs.list()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> Dict[str, Any]:
        job = state_ref.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    @app.exception_handler(camera.CameraError)
    def _camera_error(_req, exc):  # pragma: no cover - routes catch it first
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # -- the built web app ------------------------------------------------
    dist = REPO_ROOT / "web" / "dist"

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_web(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        if dist.is_dir():
            candidate = (dist / full_path).resolve()
            try:
                candidate.relative_to(dist.resolve())
            except ValueError:
                candidate = dist / "index.html"
            if full_path and candidate.is_file():
                ctype = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
                return FileResponse(str(candidate), media_type=ctype)
            index = dist / "index.html"
            if index.is_file():
                return FileResponse(str(index), media_type="text/html")
        return JSONResponse(
            {"detail": "web/dist not built yet - run `npm run build` in web/"},
            status_code=404,
        )

    return app


def __getattr__(name: str) -> Any:
    """Lazy ``app`` so importing this module (tests, CLI) creates no data dir.

    ``uvicorn cinema.app:app`` still works: uvicorn reads the attribute, which
    builds the application on first access.
    """
    if name == "app":
        return create_app()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
