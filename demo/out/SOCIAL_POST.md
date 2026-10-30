# Social posts — Aither Cinema Studio

Video: `demo/out/aither_cinema_studio_demo.mp4` (1:54, 1920x1080)
Short vertical cut: `demo/out/madness_teaser_45s_vertical.mp4` (0:45, 9:16)
Repo: https://github.com/Aitherium/aither-cinema-studio

---

## X (attached: the vertical teaser)

I built **Aither Cinema Studio** for the Comfy Open Source Higgsfield challenge: an
open-weights film set on ComfyUI, where an AI agent directs the camera.

Not prompt soup. You pick a move, a lens, a speed, a framing — the rig compiles it to real
camera conditioning — then you re-shoot the same scene from a new angle, grade it and cut it
on the beat. Every shot in my music video was directed this way.

The part I actually care about: every studio control is also a **WebMCP tool**. So an agent
can direct the shoot alongside you — Claude Code drove the same tools from the terminal while
the browser sat on the same shot.

Wan 2.2 Fun Camera + lightx2v 4-step, LTX-2, SDXL. Cloud or your own box. All open weights,
MIT, no account, no credit meter.

Made with @ComfyUI · 1:54 demo + repo below.

https://github.com/Aitherium/aither-cinema-studio

#OpenSourceHiggsfield #ComfyUI #AIvideo

---

## LinkedIn (attached: the 1:54 demo)

**An open-weights film set where AI agents direct the camera.**

For the Comfy Open Source Higgsfield challenge I built Aither Cinema Studio: a browser studio
that runs real film shots on ComfyUI, Cloud or local, and exposes every control as a WebMCP
tool so an agent can direct alongside a human.

Three things make it different from "type a prompt, get a clip":

- **A camera vocabulary, compiled.** 21 moves, 7 lenses, 3 speeds, 10 framings become real
  camera conditioning in the graph — not adjectives in a prompt.
- **Directable shots, not one-off renders.** Re-shoot the same keyframe from a new angle,
  grade per frame (Teal & Orange, Bleach Bypass, Cross Process), then cut a sequence on the beat.
- **One tool surface.** The React tab, a FastAPI backend, a cinema-mcp server, a Claude Code
  skill and an awdk agent pack reach the same verbs, so an agent is a first-class director.

Stack: Wan 2.2 Fun Camera + lightx2v 4-step, LTX-2, SDXL RealVisXL, and a ComfyUI-CinemaRig
node pack; ComfyUI is called over HTTP as a GPL-3.0 server, never linked, so the MIT licence
stands. Open weights only, no account, no credit meter.

Demo (1:54) and code: https://github.com/Aitherium/aither-cinema-studio

#OpenSourceHiggsfield #ComfyUI #GenerativeAI #OpenSource
