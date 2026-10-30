#!/usr/bin/env python3
"""Render every shot of "madness." v3 -- T2V only, world-first.

v2 rendered an SDXL keyframe and then a Wan Fun-Camera i2v clip from it.  Two
problems followed from that: the keyframe step put a face in every shot, and the
i2v conditioning holds the first frame, so a 3 s clip of a head barely moves.
v3 drops the keyframe step entirely and renders text->video with strong subject
motion named in every prompt.

Engine mix, per shot, from demo/shotlist_v3.json:
  wan_t2v  Wan 2.2 t2v + lightx2v 4-step LoRAs, 832x480, 81 frames (5.06 s @16)
  ltx      LTX-2 t2v, 768x512, 97 frames (4.04 s @24)

    python demo/render_v3.py                     # everything, 6 at a time
    python demo/render_v3.py --shots s01,s04 -v
    python demo/render_v3.py --concurrency 4     # if Cloud queues

Idempotent: a shot whose clip.mp4 probes as a real video is skipped.
Progress is appended to demo/render_log_v3.jsonl; a failed job is retried twice.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

DEMO = Path(__file__).resolve().parent
sys.path.insert(0, str(DEMO))

from render_all import env, probe, run_cli  # noqa: E402

SHOTS_DIR = DEMO / "shots_v3"
LOG = DEMO / "render_log_v3.jsonl"
WORK = DEMO / ".renderdata_v3"


def log(entry: Dict[str, Any], path: Path = LOG) -> None:
    entry = dict(entry)
    entry.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")

CLIP_W, CLIP_H = 832, 480
LTX_W, LTX_H = 768, 512
WAN_FRAMES = 81      # 5.06 s @ 16 fps
LTX_FRAMES = 97      # 4.04 s @ 24 fps


def argv_for(shot: Dict[str, Any]) -> Tuple[List[str], int, int]:
    engine = str(shot["engine"])
    frames = LTX_FRAMES if engine == "ltx" else WAN_FRAMES
    w, h = (LTX_W, LTX_H) if engine == "ltx" else (CLIP_W, CLIP_H)
    steps = 8 if engine == "ltx" else 4
    argv = [
        "render", "--mode", "t2v", "--engine", engine,
        "--prompt", shot["prompt"],
        "--negative", shot["negative"],
        "--move", str(shot.get("move") or "static"),
        "--lens", str(shot.get("lens") or "24mm"),
        "--speed", str(shot.get("speed") or "fast"),
        "--framing", str(shot.get("framing") or "wide"),
        "--width", str(w), "--height", str(h),
        "--frames", str(frames), "--steps", str(steps),
        "--seed", str(shot["seed"]),
        "--out", "",  # filled by caller
    ]
    return argv, w, h


def render_one(shot: Dict[str, Any], verbose: bool) -> Dict[str, Any]:
    sid = shot["id"]
    out_dir = SHOTS_DIR / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "clip.mp4"
    data_dir = WORK / sid
    data_dir.mkdir(parents=True, exist_ok=True)

    if target.is_file() and probe(target, video=True):
        return {"shot": sid, "status": "skipped", "out": str(target)}

    argv, w, h = argv_for(shot)
    argv += ["--data-dir", str(data_dir)]
    argv[argv.index("")] = str(target)

    last_err = ""
    for attempt in range(1, 4):
        seed_shot = dict(shot)
        if attempt > 1:
            # motion QA rejected this shot: new seed so the retry is a new take
            seed_shot["seed"] = int(shot["seed"]) + attempt * 1000
        argv2, _, _ = argv_for(seed_shot)
        argv2 += ["--data-dir", str(data_dir)]
        argv2[argv2.index("")] = str(target)
        t0 = time.time()
        try:
            code, tail = run_cli(argv2, timeout_s=1800.0)
        except subprocess.TimeoutExpired:
            code, tail = 124, "timeout"
        elapsed = round(time.time() - t0, 1)
        info = probe(target, video=True) if code == 0 else None
        if code == 0 and info:
            entry = {
                "shot": sid, "status": "done", "attempt": attempt, "elapsed_s": elapsed,
                "seed": seed_shot["seed"], "engine": shot["engine"], "out": str(target),
                "width": info["width"], "height": info["height"],
                "duration_s": info["duration_s"], "bytes": info["bytes"],
            }
            log(entry, LOG)
            if verbose:
                print("  %s %-7s done %6.1fs %.2fs %dx%d" % (
                    sid, shot["engine"], elapsed, info["duration_s"], info["width"], info["height"]),
                    flush=True)
            return entry
        last_err = "%s (exit %s)" % (tail.strip().splitlines()[-1] if tail.strip() else "no output", code)
        log({"shot": sid, "status": "retry", "attempt": attempt,
             "elapsed_s": elapsed, "error": last_err}, LOG)
        if verbose:
            print("  %s attempt %d failed: %s" % (sid, attempt, last_err), flush=True)
        time.sleep(2.0 * attempt)

    log({"shot": sid, "status": "failed", "error": last_err}, LOG)
    return {"shot": sid, "status": "failed", "error": last_err}


def main() -> int:
    ap = argparse.ArgumentParser(description="render every shot of madness. v3")
    ap.add_argument("--shotlist", default=str(DEMO / "shotlist_v3.json"))
    ap.add_argument("--shots", default="", help="comma-separated ids; default all")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", "-j", type=int, default=6)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    doc = json.loads(Path(args.shotlist).read_text(encoding="utf-8"))
    shots = list(doc["shots"])
    if args.shots:
        want = {s.strip() for s in args.shots.split(",") if s.strip()}
        shots = [s for s in shots if s["id"] in want]
    if args.limit:
        shots = shots[: args.limit]
    if args.force:
        for s in shots:
            p = SHOTS_DIR / s["id"] / "clip.mp4"
            if p.is_file():
                p.unlink()

    env()  # fail fast if the key is missing
    print("madness. v3 -- %d shots, %d at a time" % (len(shots), args.concurrency), flush=True)
    t0 = time.time()
    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {pool.submit(render_one, s, args.verbose): s for s in shots}
        for f in as_completed(futs):
            results.append(f.result())
    done = [r for r in results if r["status"] in ("done", "skipped")]
    failed = [r for r in results if r["status"] == "failed"]
    print("\n%d/%d rendered in %.0fs; failed: %s" % (
        len(done), len(results), time.time() - t0,
        ", ".join(r["shot"] for r in failed) or "none"), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
