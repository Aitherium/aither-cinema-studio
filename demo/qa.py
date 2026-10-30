#!/usr/bin/env python3
"""Look at every shot before the cut does.

Pulls a mid frame and a last frame from each clip, measures brightness and
contrast (a black shot, a noise field or a flat colour field is detectable
without eyes), tiles the mid frames into demo/contact_sheet.jpg, and prints a
table of anything that looks broken so it can be re-rendered with
``python demo/render_all.py --shots <ids> --force``.

    python demo/qa.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

DEMO = Path(__file__).resolve().parent
SHOTS = DEMO / "shots"
QA = DEMO / ".qa"
SHEET = DEMO / "contact_sheet.jpg"


def ffprobe_dur(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip() or 0.0)
    except ValueError:
        return 0.0


def frame_stats(path: Path) -> Tuple[float, float]:
    """(mean luma, std luma) of a still, via ffmpeg signalstats."""
    # metadata=print writes at INFO level, so -v must stay at info or the stats vanish
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vf", "signalstats,metadata=print",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    mean = std = ymin = ymax = None
    for line in (out.stderr or "").splitlines():
        if "YAVG=" in line:
            mean = float(line.rsplit("=", 1)[1])
        if "YSTD=" in line:
            std = float(line.rsplit("=", 1)[1])
        if "YMIN=" in line:
            ymin = float(line.rsplit("=", 1)[1])
        if "YMAX=" in line:
            ymax = float(line.rsplit("=", 1)[1])
    # some ffmpeg builds emit no YSTD; YMAX-YMIN is the same idea (luma spread)
    if std is None and ymin is not None and ymax is not None:
        std = ymax - ymin
    return (mean if mean is not None else -1.0, std if std is not None else -1.0)


def cut_sheet(master: Path, out: Path, n: int = 10) -> Dict[str, Any]:
    """Ten frames spread across the finished cut, labelled with their time.

    The one question the per-shot sheet cannot answer: does the FILM read as
    trippy at a glance?  Saves demo/contact_sheet_v2.jpg.
    """
    dur = ffprobe_dur(master)
    if dur <= 0:
        return {"error": "no master", "path": str(master)}
    times = [round(dur * (i + 0.5) / n, 3) for i in range(n)]
    tmp = [QA / ("cut_%02d.png" % i) for i in range(n)]
    for t, p in zip(times, tmp):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % t, "-i", str(master),
                        "-frames:v", "1", str(p)], capture_output=True)
    have = [(t, p) for t, p in zip(times, tmp) if p.is_file()]
    args: List[str] = []
    filt: List[str] = []
    for i, (t, p) in enumerate(have):
        args += ["-i", str(p)]
        # the filtergraph parser splits options on ':' even inside quotes, so the
        # mm:ss label has to carry escaped colons
        lab = ("%d:%05.2f" % (int(t) // 60, t % 60)).replace(":", r"\:")
        filt.append("[%d:v]scale=384:-1,drawtext=text='%s':x=8:y=8:fontsize=22:"
                    "fontcolor=white:box=1:boxcolor=black@0.65[t%d]" % (i, lab, i))
    cols = 5
    layout = "|".join("%d_%d" % ((i % cols) * 384, (i // cols) * 216) for i in range(len(have)))
    # xstack takes no fill option (that is hstack/vstack); every tile is the same
    # size here so the grid needs no filler anyway.
    filt.append("".join("[t%d]" % i for i in range(len(have))) +
                "xstack=inputs=%d:layout=%s[v]" % (len(have), layout))
    r = subprocess.run(["ffmpeg", "-y", "-v", "error"] + args +
                       ["-filter_complex", ";".join(filt), "-map", "[v]", "-frames:v", "1",
                        "-q:v", "3", str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    stats = []
    for t, p in have:
        mean, std = frame_stats(p)
        stats.append({"t": t, "mean": round(mean, 1), "spread": round(std, 1)})
    return {"sheet": str(out) if r.returncode == 0 else None,
            "error": (r.stderr or "")[-500:] if r.returncode else None,
            "duration_s": dur, "frames": stats}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="QA the shots, or the finished cut")
    ap.add_argument("--cut", action="store_true", help="sheet 10 frames of the finished cut")
    ap.add_argument("--master", default=str(DEMO / "out" / "madness_music_video.mp4"))
    args = ap.parse_args()
    QA.mkdir(parents=True, exist_ok=True)
    if args.cut:
        res = cut_sheet(Path(args.master), DEMO / "contact_sheet_v2.jpg")
        print(json.dumps(res, indent=1))
        return 0 if res.get("sheet") else 1

    doc = json.loads((DEMO / "shotlist.json").read_text(encoding="utf-8"))
    shots = sorted(doc["shots"], key=lambda s: float(s["start_s"]))
    QA.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []

    for s in shots:
        sid = s["id"]
        clip = SHOTS / sid / "clip.mp4"
        key = SHOTS / sid / "key.png"
        mid = QA / ("%s_mid.png" % sid)
        row: Dict[str, Any] = {"id": sid, "section": s["section"], "problems": []}
        if not clip.is_file():
            row["problems"].append("no clip")
            rows.append(row)
            continue
        dur = ffprobe_dur(clip)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % (dur / 2.0), "-i", str(clip),
             "-frames:v", "1", str(mid)],
            capture_output=True,
        )
        if not mid.is_file():
            row["problems"].append("no mid frame")
            rows.append(row)
            continue
        mean, std = frame_stats(mid)
        row.update({"clip_s": round(dur, 2), "mean": round(mean, 1), "std": round(std, 1)})
        if mean < 12:
            row["problems"].append("very dark (mean %.0f)" % mean)
        if std < 10:
            row["problems"].append("flat/noise (spread %.1f)" % std)
        if dur < 0.5:
            row["problems"].append("clip too short")
        if key.is_file():
            kmean, kstd = frame_stats(key)
            row.update({"key_mean": round(kmean, 1), "key_std": round(kstd, 1)})
            if kstd < 10:
                row["problems"].append("keyframe flat")
        else:
            row["problems"].append("no keyframe")
        rows.append(row)

    (QA / "qa.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")

    mids = [QA / ("%s_mid.png" % s["id"]) for s in shots if (QA / ("%s_mid.png" % s["id"])).is_file()]
    if mids:
        args: List[str] = []
        for p in mids:
            args += ["-i", str(p)]
        n = len(mids)
        cols = 6
        rows_n = (n + cols - 1) // cols
        # one label + image per tile, scaled to 320 wide
        filt = []
        for i in range(n):
            filt.append("[%d:v]scale=320:-1,drawtext=text='%s':x=6:y=6:fontsize=20:"
                        "fontcolor=white:box=1:boxcolor=black@0.6[t%d]" % (i, mids[i].stem.split("_")[0], i))
        layout = "|".join("%d_%d" % ((i % cols) * 320, (i // cols) * 180) for i in range(n))
        filt.append("".join("[t%d]" % i for i in range(n)) +
                    "xstack=inputs=%d:layout=%s:fill=black[v]" % (n, layout))
        cmd = ["ffmpeg", "-y", "-v", "error"] + args + [
            "-filter_complex", ";".join(filt), "-map", "[v]", "-frames:v", "1",
            "-q:v", "3", str(SHEET)]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print("contact sheet failed: %s" % r.stderr[-800:], file=sys.stderr)
        else:
            print("contact sheet: %s (%d shots, %dx%d grid)" % (SHEET, n, cols, rows_n))

    bad = [r for r in rows if r["problems"]]
    print("%d shots, %d flagged" % (len(rows), len(bad)))
    for r in bad:
        print("  %-4s %-6s %s" % (r["id"], r["section"], "; ".join(r["problems"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
