# cinema-mcp

The MCP server for [Aither Cinema Studio](../README.md). Ten tools over the local
studio backend (`CINEMA_URL`, default `http://127.0.0.1:8765`), for any agent
that speaks MCP.

```bash
pip install -e ".[dev]"   # or: pip install -e "mcp/[dev]" from the repo root
cinema-mcp --check        # 0 ready · 1 backend unreachable · 2 ComfyUI not reachable
cinema-mcp                # stdio (the default; what MCP clients spawn)
cinema-mcp --transport streamable-http --port 8788
```

Flags: `--transport stdio|streamable-http`, `--host`, `--port`, `--url`
(overrides `CINEMA_URL`), `--check`.

## Tools

| tool | arguments | returns |
|---|---|---|
| `list_camera_moves` | — | moves, lenses, speeds, framings, defaults |
| `plan_shot` | `prompt`, `camera_rig?`, `engine?`, `quality?` | compiled prompt + rig summary, **no render** |
| `render_shot` | `engine`, `camera_rig?`, `prompt?`, `start_media_id?`, `end_media_id?`, `negative?`, `seed?`, `quality?` | `shot_id`, `job_id` |
| `reshoot_shot` | `source_media_id`, `camera_rig?`, `at_s?`, `prompt?`, `seed?`, `quality?` | new shot from the same frame |
| `grade_shot` | `shot_id`, `look`, `strength?` | grade job |
| `list_shots` | `limit?` | shots with absolute `media_url` |
| `get_shot` | `shot_id` | status, progress, media ids, compiled prompt |
| `cut_sequence` | `shot_ids` | cut job (order = the edit) |
| `cut_music_video` | `shot_ids`, `bpm?`, `beats_per_shot?`, `track_duration_s?`, `audio_media_id?`, `fit?` | beat sheet + cut job (muxes the track when `audio_media_id` is given) |
| `backend_status` | — | backend → ComfyUI → grade chain |

`engine` is required on `render_shot`: `keyframe`, `t2v`, `i2v`, `flf2v`.
Camera rig ids come from `list_camera_moves`; an unknown id raises with the valid
set named.

## Two things the tools are honest about

- **`cut_music_video` muxes only when you hand it the audio.** Upload the track,
  pass its `audio_media_id`, and the backend retimes every shot to
  `beats * 60/bpm` and muxes picture with the song (`fit: "audio"` stretches the
  last shot so the picture ends with the track). Without `audio_media_id` the cut
  is still retimed to the beat sheet, but silent. The beat sheet is always
  returned as the plan (`per_shot_s`, each shot's `start_s`/`end_s`, `track_fit`).
- **`plan_shot` compiles locally** from the vocabulary the backend serves, and a
  parity test pins its output byte-for-byte against `cinema.camera.compile`, so
  drift between the MCP package and the studio fails CI.

## Tests

```bash
python -m pytest -q       # tools, protocol, prompt parity
python -m ruff check .
```

The suite runs against a real HTTP fake backend on an ephemeral port (no GPU, no
ComfyUI), plus in-memory JSON-RPC sessions through the MCP SDK to prove every
tool is registered with a usable schema.
