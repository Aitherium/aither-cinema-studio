---
name: aither-cinema
description: Direct film shots through a local ComfyUI set — plan a shot from a camera rig, render it, re-shoot from a new angle, grade it, cut a sequence or a beat-timed music video. Use when the user asks for video, a shot, a scene, a sequence, a music video, or "reshoot that from another angle", and the cinema-mcp tools are available.
---

# Aither Cinema Studio

A camera rig — move, lens, speed, framing — compiled into a prompt clause and
rendered on ComfyUI. The rig **is** the direction: you choose it, the studio
compiles it, the model obeys it.

Everything here goes through the `cinema-mcp` tools (`mcp__cinema__*`). They
wrap a local FastAPI backend at `http://127.0.0.1:8765` (`CINEMA_URL`).

## Start it

```bash
# 1. backend (once; leave it running)
cd server && python -m venv .venv && .venv/Scripts/python -m pip install -e .
.venv/Scripts/python -m uvicorn cinema.app:app --port 8765

# 2. check the chain: backend -> ComfyUI -> grade CLI
cinema-mcp --check
```

`--check` exits 1 when the backend is unreachable, 2 when it is up but ComfyUI
is not (`ready_to_render: false`), 0 when a render can actually run. Run it
before blaming a tool.

If the tools are missing from the session, the MCP server is not registered —
install it (`pip install -e mcp/` from the repo root) and copy this skill with
`cp -r skills/aither-cinema ~/.claude/skills/`, then merge
`agents/claude-code/.mcp.json` into the project's `.mcp.json`.

## Know the vocabulary first

Call `list_camera_moves` once per session. Never invent a move.

- **moves** — `static`, `dolly_in`, `dolly_out`, `truck_left`, `truck_right`,
  `pan_left`, `pan_right`, `tilt_up`, `tilt_down`, `crane_up`, `crane_down`,
  `orbit_left`, `orbit_right`, `handheld`, `push_in_fast`, `whip_pan`, `arc`,
  `rack_focus`, `zoom_in`, `zoom_out`
- **lenses** — `14mm`, `24mm`, `35mm`, `50mm` (default), `85mm`, `135mm`,
  `anamorphic`
- **speeds** — `slow` (default), `medium`, `fast`
- **framings** — `extreme_wide`, `wide`, `medium` (default), `close_up`,
  `extreme_close_up`, `over_the_shoulder`, `low_angle`, `high_angle`,
  `birds_eye`, `dutch`
- **engines** (`render_shot`) — `keyframe` (still, no camera motion), `t2v`,
  `i2v` (needs `start_media_id`), `flf2v` (needs both ends)
- **looks** (`grade_shot`) — `none`, `warm`, `cool`, `tealOrange`,
  `bleachBypass`, `fadedFilm`, `dayForNight`, `monoContrast`, `crossProcess`

Habits that make a difference: one move per shot (a rig is a single decision,
not a stack), `slow` + `dolly_in` for tension, `whip_pan` + `fast` only at a cut,
`85mm` + `close_up` for a face, `14mm` + `low_angle` for scale.

## Recipe — direct a 4-shot sequence

1. `plan_shot` for each beat, **before** spending a render:

   ```
   plan_shot(prompt="a lighthouse keeper steps onto the jetty at dawn",
             camera_rig={"move": "crane_up", "lens": "24mm", "framing": "extreme_wide"},
             engine="t2v", quality="draft")
   ```

   It returns the compiled prompt and a `rig_summary`. Read them back to the
   user — that is the shot list, and it cost nothing.

2. `render_shot` with the same engine and `camera_rig` for each planned shot,
   collecting `shot_id`s in screen order. `quality="draft"` first, always.

3. `get_shot` each id and report the `media_url` — the user judges the frames,
   not your description of them. Re-render the ones that miss (a new `seed` or a
   tighter framing), then move on.

4. `cut_sequence(shot_ids=[...])` in screen order — the order you pass **is**
   the edit. Then `get_shot` shows nothing new; poll `backend_status` or the
   returned `job_id` until the cut's media appears.

## Recipe — re-shoot from a new angle

1. `get_shot(shot_id)` and take its `media_id` — the *source*, not the shot id.
2. `reshoot_shot(source_media_id=..., camera_rig={"move": "orbit_right",
   "lens": "85mm", "framing": "close_up"}, at_s=1.5)`.

The frame at `at_s` (video; default the last frame) or the image itself is
lifted and re-animated with **your** rig, so the scene returns from a new angle
rather than being rerolled. Use it to get coverage of one action: wide, then
`over_the_shoulder`, then `close_up`, all at the same `at_s`.

## Recipe — music video from a track

1. Ask for, or measure, the tempo and the track length.
2. Render the shots in song order (a chorus can reuse a shot — pass the same
   `shot_id` twice; the cut repeats it).
3. `cut_music_video(shot_ids=[...], bpm=120, beats_per_shot=4,
   track_duration_s=214.0)`.

It returns `per_shot_s`, a `cut_sheet` with each shot's `start_s`/`end_s`, and
`track_fit` (`fits` / `overruns` / `short`) with advice — lower
`beats_per_shot` if it overruns. The backend has **no audio mux**: the cut is
picture only, and every clip is normalised to its own length. Mux the track on
the user's machine:

```bash
ffmpeg -i cut.mp4 -i track.wav -c:v copy -c:a aac -shortest music-video.mp4
```

Say that plainly instead of implying the tool made a scored film.

## Film grade

`grade_shot(shot_id, look="tealOrange", strength=0.8)` — the graded cut becomes
`graded_media_id`, and `cut_sequence` prefers graded media when a shot has it.
`0.8` is a film grade; `1.0` is a look; use `bleachBypass` or `fadedFilm` for a
period feel, `dayForNight` to sell night from a day plate. Grade **before** you
cut, or the sequence mixes graded and ungraded shots.

## Reporting

Give the user `media_url` links and the rig you used, one line per shot. Do not
describe a frame you have not fetched, and do not claim a render succeeded
because a tool returned a `job_id` — `get_shot` says `done`.
