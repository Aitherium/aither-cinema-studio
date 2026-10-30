"""Narration script for the contest demo video (<2 min).

One entry per beat: key, caption kicker (burned above the caption), the line as
SPOKEN (phonetic spellings for the local TTS: acronyms spaced, "Ayther"), and the
line as CAPTIONED (correct spelling, burned on screen).

The build script synthesises each spoken line with local Kokoro ONNX and lays it on
the segment timeline; a line that overruns its slot is re-synthesised slightly
faster rather than cut mid-sentence.
"""

VOICES = "am_michael"  # calm, confident, neutral American read

LINES = [
    (
        "hook",
        "AITHER CINEMA STUDIO",
        "Every shot in this film was directed by an A I agent. Not prompted. "
        "Directed, on a real camera rig.",
        "Every shot in this film was directed by an AI agent. Not prompted — directed, on a real camera rig.",
    ),
    (
        "settings",
        "THE SET",
        "Ayther Cinema Studio is an open-weights film set. Point it at Comfy U I, "
        "Cloud or your own box, and the studio is live.",
        "Aither Cinema Studio is an open-weights film set. Point it at ComfyUI — Cloud or your own box — and the studio is live.",
    ),
    (
        "agent",
        "AN AGENT DIRECTS",
        "An agent plans a shot over Web M C P. Watch the feed: it sets the camera, "
        "then renders. That is a real Comfy Cloud render.",
        "An agent plans a shot over WebMCP. Watch the feed: it sets the camera, then renders — a real Comfy Cloud render.",
    ),
    (
        "reshoot",
        "RE-SHOOT",
        "Now cover the scene. Re-shoot the same shot from a new angle: "
        "orbit left, eighty-five millimetre, low angle.",
        "Now cover the scene. Re-shoot the same shot from a new angle: orbit left, 85mm, low angle.",
    ),
    (
        "grade",
        "GRADE",
        "Grade it in one click. Teal and orange, per frame, on your own machine.",
        "Grade it in one click — Teal & Orange, per frame, on your own machine.",
    ),
    (
        "cut",
        "CUT ON THE BEAT",
        "Cut on the beat and the sequence is done. Claude Code drives "
        "the same tools from the terminal.",
        "Cut on the beat and the sequence is done. Claude Code drives the same tools from the terminal.",
    ),
    (
        "diagram",
        "HOW IT IS BUILT",
        "Under the hood, a React studio exposes every control as a Web M C P tool, "
        "over a Fast A P I backend, to Comfy's H T T P A P I. Wan two point two, "
        "L T X two, and S D X L.",
        "Under the hood: every control is a WebMCP tool, over a FastAPI backend, to Comfy's HTTP API — Wan 2.2 Fun Camera + lightx2v 4-step, LTX-2, SDXL.",
    ),
    (
        "why",
        "WHY IT IS INNOVATIVE",
        "Why it is innovative. One: every control is a tool, so any agent can "
        "direct. Two: a camera vocabulary compiles to real conditioning, not "
        "prompt soup. Three: one tool surface, browser to node pack.",
        "Why it is innovative: 1) every control is a tool, so any agent can direct. 2) a camera vocabulary compiles to real camera conditioning, not prompt soup. 3) one tool surface spans browser, MCP and a node pack.",
    ),
    (
        "payoff",
        "OPEN WEIGHTS ONLY",
        "Open weights only. M I T. Clone it, point it at your Comfy U I, and direct.",
        "Open weights only. MIT. Clone it, point it at your ComfyUI, and direct.",
    ),
]
