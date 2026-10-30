#!/usr/bin/env python3
"""Render every shot of "madness." through Aither Cinema Studio.

One shot = one SDXL keyframe (16:9) + one Wan 2.2 Fun Camera clip that starts
from that keyframe and carries the shot's camera rig for the shot's length.

Run from anywhere; it drives the studio's own CLI (``python -m cinema.cli
render``) against Comfy Cloud, one subprocess per job, each with its own data
dir so concurrent jobs never race on the studio's JSON job store.

    python demo/render_all.py                      # everything, 4 at a time
    python demo/render_all.py --shots s01,s02 -v   # a slice
    python demo/render_all.py --keyframes-only     # stills first
    python demo/render_all.py --concurrency 8      # if the cloud queues, it waits

Idempotent: a shot whose clip.mp4 probes as a real video is skipped.
Progress is appended to demo/render_log.jsonl; a failed job is retried twice.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEMO = Path(__file__).resolve().parent
REPO = DEMO.parent
SERVER = REPO / "server"
SHOTS_DIR = DEMO / "shots"
LOG = DEMO / "render_log.jsonl"
WORK = DEMO / ".renderdata"

# One aspect for the whole film: 16:9. Keyframes are SDXL's cheap 16:9 bucket
# (589k px, the same budget as the proven 768x768 render); the clips are Wan's
# 832x480, which is the same frame shape one step down so nothing is re-cropped.
KEY_W, KEY_H = 1024, 576
CLIP_W, CLIP_H = 832, 480
FPS = 16              # what every engine actually emits (measured)
WAN_MAX_FRAMES = 81   # one 81-frame clip is 5.06 s at 16 fps

# SDXL reads the head of the prompt hardest, and a detailed face description
# otherwise wins every time: every "extreme wide" came back as a bust portrait
# until the scale was stated first. These prefixes go IN FRONT of the shot's own
# written prompt, which stays verbatim.
FRAMING_PREFIX = {
    "extreme_wide": "extreme wide establishing shot, vast scale, the environment fills the frame, the figure is tiny, seen from behind",
    "wide": "wide shot, the environment dominates the frame, the figure small in frame",
    "birds_eye": "high aerial wide shot seen from far above, the figure tiny, the set filling the frame",
    "high_angle": "wide shot from high above, the figure small in frame",
    "low_angle": "low angle shot, the figure looming against the set",
    "over_the_shoulder": "over-the-shoulder shot, the set filling most of the frame",
    "dutch": "dutch angle, tilted frame",
    "medium": "",
    "close_up": "",
    "extreme_close_up": "",
}
# One palette for the whole film (the treatment: ink-black rock, bone dust, one
# hot accent). Left to itself SDXL answers a hooded man in a cave with a bright
# blue-sky location shoot, so the look is stated on every frame and the daylight
# end of the palette is named in the negative.
LOOK_TAIL = (
    "cinematic film still, heavy 35mm film grain, deep black shadows, inky blacks, "
    "desaturated palette, low key lighting, chiaroscuro, volumetric dust"
)
PALETTE_NEG = "blue sky, bright daylight, sunny, lush green grass, oversaturated, HDR, flat lighting"
# The 2026-10-30 psychedelic pass (the owner: "make it trippy - bring them into the
# world"): a shot with "look": "psychedelic" gets the neon tail and a negative that
# pushes AWAY from the film look -- the old tail's "desaturated palette" and the old
# negative's "oversaturated" were exactly what flattened the first cut.
PSY_LOOK_TAIL = (
    "cinematic film still with saturated neon color, magenta cyan acid green and amber, "
    "prismatic light leaks, liquid chrome reflections, bioluminescent glow, kaleidoscopic "
    "fractal detail, deep black shadows, heavy 35mm film grain, high dynamic range"
)
PSY_PALETTE_NEG = (
    "desaturated, dull colors, grey, washed out, flat lighting, muddy palette, "
    "monochrome, blue sky, bright daylight"
)
# Shots that needed a clarifying clause after a look at the frame (SDXL reads
# "porcelain mask" as the Guy Fawkes one; a public jury should see porcelain).
KEY_EXTRA: Dict[str, str] = {}

# Full rewrites where a phrase keeps pulling a branded image. s45's "porcelain
# mask" renders the Guy Fawkes mask however it is qualified; the shot only needs
# "masks", so it is described as blank plaster face moulds instead.
KEY_PROMPT_OVERRIDE = {
    "s45": (
        "a carousel of blank white plaster face moulds circling the same hero: a hooded young man "
        "in his mid-twenties, dark grey hood up, short dark hair, pale grey eyes, three-day stubble, "
        "a small scar through the left eyebrow, lean, hands in pockets, and beneath each mould a small "
        "empty bird made of folded paper, nothing organic, nothing bleeding, the moulds are smooth and "
        "featureless like a mannequin head with hollow eye sockets"
    ),
    # s19 and s30 moved into shotlist.json's own prompts for the 2026-10-30
    # psychedelic pass; an override here would silently beat the new prompt.
}

# The wide shots need the portrait autopilot switched off; the close-ups need the face.
NEG_WIDE = (
    "gun, firearm, pistol, rifle, blood, wound, gore, injury, noose, grave, corpse, "
    "drugs, syringe, self-harm, blade, knife, portrait, headshot, selfie, "
    "close-up face filling the frame, blurry, distorted, deformed, extra limbs, "
    "text, watermark, logo, nudity, " + PALETTE_NEG
)
NEG_CLOSE = (
    "gun, firearm, pistol, rifle, blood, wound, gore, injury, noose, grave, corpse, "
    "drugs, syringe, self-harm, blade, knife, blurry, distorted, deformed, extra limbs, "
    "text, watermark, logo, nudity, " + PALETTE_NEG
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def log(entry: Dict[str, Any]) -> None:
    entry = dict(entry)
    entry.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def probe(path: Path, video: bool = False) -> Optional[Dict[str, Any]]:
    """ffprobe a file; None if it is not a readable video/still.

    A PNG has no duration field -- ``video=False`` accepts geometry alone, while
    ``video=True`` also demands a real duration (a truncated mp4 has none).
    """
    if not path.is_file() or path.stat().st_size < 1024:
        return None
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,nb_frames,r_frame_rate",
                "-show_entries", "format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        doc = json.loads(out.stdout or "{}")
        stream = (doc.get("streams") or [{}])[0]
        dur = float((doc.get("format") or {}).get("duration") or 0.0)
    except (ValueError, IndexError):
        return None
    w, h = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if w < 64 or h < 64:
        return None
    if video and dur <= 0.2:
        return None
    return {"width": w, "height": h, "duration_s": round(dur, 3), "bytes": path.stat().st_size}


def env() -> Dict[str, str]:
    e = dict(os.environ)
    e.setdefault("COMFY_BASE_URL", "https://cloud.comfy.org")
    e["COMFY_SUBMIT_TIMEOUT_S"] = e.get("COMFY_SUBMIT_TIMEOUT_S", "900")
    key_file = e.get("COMFY_API_KEY_FILE") or os.path.expanduser("~/.aither/comfy_api_key")
    e["COMFY_API_KEY_FILE"] = key_file
    if not Path(key_file).is_file() and not e.get("COMFY_API_KEY"):
        raise SystemExit("no API key: set COMFY_API_KEY_FILE (looked at %s)" % key_file)
    e["PYTHONIOENCODING"] = "utf-8"
    return e


def run_cli(argv: List[str], timeout_s: float = 1800.0) -> Tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "cinema.cli"] + argv,
        cwd=str(SERVER), env=env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout_s,
    )
    tail = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, tail[-4000:]


def frames_for(duration_s: float) -> int:
    """Wan wants 4n+1 frames; ask for at least the shot's length at 16 fps."""
    want = int(round(duration_s * FPS)) + 1
    want = max(17, min(WAN_MAX_FRAMES, want))
    rest = (want - 1) % 4
    if rest:
        want += (4 - rest) if rest >= 2 else -rest
    return max(17, min(WAN_MAX_FRAMES, want))


def seed_for(shot: Dict[str, Any], stage: str) -> int:
    """Fixed per shot (and per stage) so a re-run reproduces the same frame."""
    idx = int(str(shot["id"]).lstrip("s") or 1)
    return (idx * 7919 + (0 if stage == "key" else 4001)) % 2_000_000_007


# --------------------------------------------------------------------------
# one stage, with retries
# --------------------------------------------------------------------------
def render_one(shot: Dict[str, Any], stage: str, verbose: bool) -> Dict[str, Any]:
    sid = shot["id"]
    out_dir = SHOTS_DIR / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / ("key.png" if stage == "key" else "clip.mp4")
    data_dir = WORK / ("%s_%s" % (sid, stage))
    data_dir.mkdir(parents=True, exist_ok=True)

    is_video = stage != "key"
    if target.is_file() and probe(target, video=is_video):
        return {"shot": sid, "stage": stage, "status": "skipped", "out": str(target)}

    rig = shot.get("camera") or {}
    framing = str(rig.get("framing") or "medium")
    seed = seed_for(shot, stage)
    psy = str(shot.get("look") or "") == "psychedelic"
    if stage == "key":
        prefix = FRAMING_PREFIX.get(framing, "")
        body = KEY_PROMPT_OVERRIDE.get(shot["id"], shot["keyframe_prompt"]).strip().rstrip(".")
        key_prompt = (
            ((prefix + ". ") if prefix else "") + body
            + KEY_EXTRA.get(shot["id"], "") + ". "
            + (PSY_LOOK_TAIL if psy else LOOK_TAIL)
        )
        wide = framing in ("extreme_wide", "wide", "birds_eye", "high_angle")
        negative = NEG_WIDE if wide else NEG_CLOSE
        if psy:
            negative = negative.replace(PALETTE_NEG, PSY_PALETTE_NEG)
        argv = [
            "render", "--mode", "keyframe", "--engine", "sdxl",
            "--prompt", key_prompt,
            "--negative", negative,
            "--move", str(rig.get("move") or "static"),
            "--lens", str(rig.get("lens") or "50mm"),
            "--speed", str(rig.get("speed") or "slow"),
            "--framing", framing,
            "--width", str(KEY_W), "--height", str(KEY_H),
            "--seed", str(seed),
            "--data-dir", str(data_dir), "--out", str(target),
        ]
    else:
        start = out_dir / "key.png"
        if not (start.is_file() and probe(start)):
            return {"shot": sid, "stage": stage, "status": "error", "error": "no keyframe"}
        frames = frames_for(float(shot["end_s"]) - float(shot["start_s"]))
        clip_neg = NEG_CLOSE
        if psy:
            clip_neg = clip_neg.replace(PALETTE_NEG, PSY_PALETTE_NEG)
        argv = [
            "render", "--mode", "i2v", "--engine", "wan_camera",
            "--prompt", shot["motion_prompt"],
            "--negative", clip_neg,
            "--start", str(start),
            "--move", str(rig.get("move") or "static"),
            "--lens", str(rig.get("lens") or "50mm"),
            "--speed", str(rig.get("speed") or "slow"),
            "--framing", str(rig.get("framing") or "medium"),
            "--width", str(CLIP_W), "--height", str(CLIP_H),
            "--frames", str(frames), "--steps", "4",
            "--seed", str(seed),
            "--data-dir", str(data_dir), "--out", str(target),
        ]

    last_err = ""
    for attempt in range(1, 4):  # one try + 2 retries
        t0 = time.time()
        try:
            code, tail = run_cli(argv)
        except subprocess.TimeoutExpired:
            code, tail = 124, "timeout"
        elapsed = round(time.time() - t0, 1)
        info = probe(target, video=is_video) if code == 0 else None
        if code == 0 and info:
            entry = {
                "shot": sid, "stage": stage, "status": "done", "attempt": attempt,
                "elapsed_s": elapsed, "seed": seed, "out": str(target),
                "engine": "sdxl" if stage == "key" else "wan_camera",
                "width": info["width"], "height": info["height"],
                "duration_s": info["duration_s"],
            }
            log(entry)
            if verbose:
                print("  %s %-4s done %6.1fs %s" % (sid, stage, elapsed, info), flush=True)
            return entry
        last_err = "%s (exit %s)" % (tail.strip().splitlines()[-1] if tail.strip() else "no output", code)
        log({"shot": sid, "stage": stage, "status": "retry", "attempt": attempt,
             "elapsed_s": elapsed, "error": last_err})
        if verbose:
            print("  %s %-4s attempt %d failed: %s" % (sid, stage, attempt, last_err), flush=True)
        time.sleep(2.0 * attempt)

    log({"shot": sid, "stage": stage, "status": "failed", "error": last_err})
    return {"shot": sid, "stage": stage, "status": "failed", "error": last_err}


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="render every shot of madness.")
    ap.add_argument("--shotlist", default=str(DEMO / "shotlist.json"))
    ap.add_argument("--shots", default="", help="comma-separated ids; default all")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", "-j", type=int, default=4)
    ap.add_argument("--keyframes-only", action="store_true")
    ap.add_argument("--clips-only", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore existing outputs")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    doc = json.loads(Path(args.shotlist).read_text(encoding="utf-8"))
    shots = sorted(doc["shots"], key=lambda s: float(s["start_s"]))
    if args.shots:
        want = {s.strip() for s in args.shots.split(",") if s.strip()}
        shots = [s for s in shots if s["id"] in want]
    if args.limit:
        shots = shots[: args.limit]
    if not shots:
        print("no shots selected", file=sys.stderr)
        return 2

    if args.force:
        for s in shots:
            for name in ("key.png", "clip.mp4"):
                p = SHOTS_DIR / s["id"] / name
                if p.exists():
                    p.unlink()

    stages: List[str] = []
    if not args.clips_only:
        stages.append("key")
    if not args.keyframes_only:
        stages.append("clip")

    totals: Dict[str, int] = {}
    t_start = time.time()
    for stage in stages:
        todo = shots
        print("[%s] %d shot(s), concurrency %d" % (stage, len(todo), args.concurrency), flush=True)
        t_stage = time.time()
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futures = {pool.submit(render_one, s, stage, args.verbose): s for s in todo}
            for fut in as_completed(futures):
                res = fut.result()
                totals[res["status"]] = totals.get(res["status"], 0) + 1
                done += 1
                if res["status"] == "failed":
                    print("  FAILED %s %s: %s" % (res["shot"], stage, res.get("error")), flush=True)
                if not args.verbose and done % 5 == 0:
                    print("  %d/%d %s" % (done, len(todo), stage), flush=True)
        stage_s = time.time() - t_stage
        print("[%s] %.1fs wall, %.1fs/shot" % (stage, stage_s, stage_s / max(1, len(todo))), flush=True)
        log({"stage": stage, "status": "phase_done", "wall_s": round(stage_s, 1),
             "shots": len(todo), "concurrency": args.concurrency})

    summary = {
        "wall_s": round(time.time() - t_start, 1),
        "counts": totals,
        "concurrency": args.concurrency,
        "shots_dir": str(SHOTS_DIR),
    }
    print(json.dumps(summary, indent=2))
    log({"status": "run_done", **summary})
    return 0 if not totals.get("failed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
