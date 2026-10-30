# Aither Cinema Studio - backend

FastAPI service that turns a camera rig plus a prompt into a ComfyUI graph,
runs it, and hands back a graded, cut sequence.  Python 3.10+.

```
server/cinema/
  comfy.py    ComfyUI HTTP client (upload, /prompt, /history, /view, /queue)
  graphs.py   graph builders -> plain {node_id: {class_type, inputs}} dicts
  camera.py   camera rig -> prompt clauses (data in camera_vocab.json)
  grade.py    film grade: grading CLI per frame (ffmpeg split/rejoin for video)
  jobs.py     JSON job store + background worker
  media.py    uploaded/produced media, one JSON index
  ffmpeg.py   frame grab, normalise, concat
  app.py      HTTP routes; serves web/dist at / when it exists
  cli.py      render | grade | probe | export-workflows
```

## Run

```bash
cd server
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # .venv/bin on Linux/macOS
.venv/Scripts/python -m uvicorn cinema.app:app --port 8765
```

Environment (all optional):

| var | default |
|---|---|
| `COMFY_BASE_URL` | `http://127.0.0.1:8188` |
| `COMFY_API_KEY` | unset (sent as `X-API-Key` when set) |
| `COMFY_CA_BUNDLE` | unset (default TLS verification) |
| `COMFY_SUBMIT_TIMEOUT_S` | `300` |
| `PHOTOCRAFT_BIN` | `photocraft-cli` on PATH, then `~/.aither/photocraft/` |
| `CINEMA_DATA_DIR` | `./data` |
| `CINEMA_MODELS` | JSON override of model filenames |
| `CINEMA_JOB_TIMEOUT_S` | `3600` |

## Routes

| method | path | body / notes |
|---|---|---|
| GET | `/api/health` | `{ok, comfy:{reachable,base,version}, grade:{available,bin}}` |
| POST | `/api/upload` | multipart `file` -> `{media_id, kind, url}` |
| GET | `/api/media` | list |
| GET | `/api/media/{id}` | file bytes |
| POST | `/api/shots` | `{mode, prompt, camera, start_media_id?, end_media_id?, source_media_id?, seed?, quality?, at_s?}` -> `{shot_id, job_id}` |
| GET | `/api/shots` | list |
| GET | `/api/shots/{id}` | status, progress, compiled_prompt, graph, media_id |
| POST | `/api/shots/{id}/grade` | `{look, strength?}` -> `{job_id, shot_id}` |
| POST | `/api/sequence` | `{shot_ids, audio_media_id?, bpm?, beats_per_shot?, fit?}` -> `{job_id, music_video, ...}`; the music fields retime each shot to `beats * 60/bpm` and mux the track |
| GET | `/api/camera/vocab` | the one camera table |
| GET | `/api/grade/looks` | the look enum |
| GET | `/api/jobs`, `/api/jobs/{id}` | progress for grade/sequence jobs |

`reshoot` takes a frame of `source_media_id` (a video: at `at_s`, default the
last frame; an image: itself) and runs image-to-video with the **new** camera
rig, so the same scene comes back from a new angle.

## CLI

```bash
python -m cinema.cli render --mode t2v --prompt "a red kite over a cold sea" \
    --move dolly_in --lens 35mm --speed slow --framing wide --quality draft --out shot.mp4
python -m cinema.cli grade shot.mp4 graded.mp4 --look tealOrange --strength 0.8
python -m cinema.cli probe                       # writes docs/comfy-probe.json
python -m cinema.cli export-workflows            # writes workflows/*.json
```

`render` uses the same `submit_shot` code path as `POST /api/shots`, with the
job store running inline.

## Tests

```bash
.venv/Scripts/python -m pytest -q
```

No GPU and no ComfyUI needed: the client is exercised against an httpx
`MockTransport`, the API against a fake Comfy, and graphs are checked for shape
(two experts, split steps, valid wiring).  The grade test runs the real CLI when
the binary exists and skips otherwise.
