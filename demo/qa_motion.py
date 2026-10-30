#!/usr/bin/env python3
"""MOTION QA -- a check that can fail.

The owner's verdict on v2: "it's just a bunch of pictures of some random dude's
face".  A still photograph repeated reads as stills no matter how the cut is
timed, so v3 is gated on measured INTER-FRAME MOTION rather than on the prompt
having said "fast".

For every clip: decode to 96x54 grayscale, and for each consecutive pair of
frames take the mean absolute difference (0-255).  The score is the mean of
those, after dropping the top decile of pairs (a flash frame must not carry a
clip).  A clip whose score is below --threshold is rejected.

    python demo/qa_motion.py --calibrate    # score v2's clips, print a threshold
    python demo/qa_motion.py               # score shots_v3, list rejects
    python demo/qa_motion.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DEMO = Path(__file__).resolve().parent
W, H = 96, 54


def frames(path: Path) -> Optional[bytes]:
    """Raw gray8 frames at 96x54; None if ffmpeg cannot decode the file."""
    try:
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-vf", "scale=%d:%d,format=gray" % (W, H),
             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0 or not p.stdout:
        return None
    return p.stdout


def score(path: Path) -> Optional[Dict[str, Any]]:
    """Mean abs frame difference, top decile of pairs dropped."""
    buf = frames(path)
    if buf is None:
        return None
    n = W * H
    total = len(buf) // n
    if total < 3:
        return None
    diffs: List[float] = []
    prev = buf[0:n]
    for i in range(1, total):
        cur = buf[i * n:(i + 1) * n]
        s = 0
        for a, b in zip(prev, cur):
            d = a - b
            s += -d if d < 0 else d
        diffs.append(s / n)
        prev = cur
    diffs.sort()
    keep = diffs[: max(1, int(len(diffs) * 0.9))]
    mean = sum(keep) / len(keep)
    return {
        "file": str(path),
        "frames": total,
        "mad_mean": round(mean, 3),
        "mad_p10": round(keep[0], 3),
        "mad_max": round(diffs[-1], 3),
        "static_frames_frac": round(sum(1 for d in diffs if d < 0.5) / len(diffs), 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(DEMO / "shots_v3"))
    ap.add_argument("--glob", default="*/clip.mp4")
    ap.add_argument("--threshold", type=float, default=3.0)
    ap.add_argument("--calibrate", action="store_true",
                    help="also score demo/shots (the v2 clips) to fix the threshold")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    rows: List[Dict[str, Any]] = []
    for p in sorted(Path(args.dir).glob(args.glob)):
        r = score(p)
        if r:
            r["id"] = p.parent.name if p.parent.name != Path(args.dir).name else p.name
            rows.append(r)

    if args.calibrate:
        print("--- v2 baseline (demo/shots/*/clip.mp4) ---")
        v2 = []
        for p in sorted((DEMO / "shots").glob("*/clip.mp4")):
            r = score(p)
            if r:
                r["id"] = p.parent.name
                v2.append(r)
                print("  %-4s mad=%.2f static=%.2f" % (r["id"], r["mad_mean"], r["static_frames_frac"]))
        if v2:
            vals = sorted(x["mad_mean"] for x in v2)
            med = vals[len(vals) // 2]
            print("v2 median mad=%.2f  (threshold should sit clearly above this)" % med)

    if not rows:
        print("MOTION QA: no clips found under %s -- COULD NOT JUDGE" % args.dir)
        return 2

    rows.sort(key=lambda r: r["mad_mean"])
    print("\n--- shots_v3 motion (lowest first) ---")
    for r in rows:
        flag = "REJECT" if r["mad_mean"] < args.threshold else "ok"
        print("  %-4s mad=%6.2f  p10=%5.2f  max=%6.2f  static=%4.2f  %s" % (
            r["id"], r["mad_mean"], r["mad_p10"], r["mad_max"], r["static_frames_frac"], flag))
    rej = [r["id"] for r in rows if r["mad_mean"] < args.threshold]
    mean = sum(r["mad_mean"] for r in rows) / len(rows)
    print("\n%d clips  mean mad=%.2f  rejects(<%.1f)=%d %s" % (
        len(rows), mean, args.threshold, len(rej), rej or ""))
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return 1 if rej else 0


if __name__ == "__main__":
    raise SystemExit(main())
