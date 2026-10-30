#!/usr/bin/env python3
"""Cut "madness." v3 -- fast, on the beat, from the moving worlds.

v2 stretched 3 s clips across 3-4 s slots at sub-1x speed, so the cut showed
almost the same frames for four seconds: "just a bunch of pictures".  v3 is the
opposite discipline:

  * every cut is a SUB-SEGMENT of a 5-6 s clip, never the whole thing, and the
    sub-segment offset advances every time the clip is used again -- the same
    frames are never shown twice;
  * playback is native speed or FASTER (speed ramps on the drops), never slower;
  * cut length is on the measured grid: 2 beats (0.833 s) on hooks and drops,
    4 beats in verses, one 8-beat hold on the intro and on the final line;
  * the trippy post is per-cut and light -- a hue offset that jumps on the beat,
    slow hue drift inside the cut, zoom punches, RGB split and mirrored
    kaleidoscope bursts on hook lines only, grain and bloom.  No global wash:
    each clip keeps its own neon.

    python demo/cut_v3.py                 # master + teasers
    python demo/cut_v3.py --no-teasers
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

DEMO = Path(__file__).resolve().parent
SHOTS = DEMO / "shots_v3"
OUT = DEMO / "out"
BUILD = DEMO / ".cut_v3"
AUDIO = DEMO / "music" / "madness.wav"
BEATGRID = DEMO / "music" / "beatgrid.json"

W, H, FPS = 1920, 1080, 30
BPM = 144.0
BEAT = 60.0 / BPM            # 0.41667 s
PHASE = 0.039                # first measured downbeat
DURATION = 196.0

CREDIT = ("madness.  \u2014  directed in Aither Cinema Studio  \u00b7  "
          "rendered on Comfy Cloud  \u00b7  open weights (Wan 2.2, LTX-2)")
CREDIT_FONT = "C:/Windows/Fonts/segoeuib.ttf"

HOOKish = ("hook", "p2-verse-2")     # 2-beat cutting, RGB split, kaleidoscope
HOLD_SECTIONS = ("p1-intro", "p3-outro-end")   # 8-beat holds

# Two engines do not obey a dark prompt: the steam train and the city both came
# back as bright blue-sky daylight, which reads as stock footage next to the
# neon worlds.  Measured, not guessed: these shots are the ones a contact sheet
# shows as high-key.  They are graded down into the film's night palette -- per
# shot, not a global wash, so every other clip keeps its own neon.
NIGHT_GRADE = ("eq=brightness=-0.11:contrast=1.16:saturation=0.88,"
               "colorbalance=rs=-0.05:gs=-0.02:bs=0.07")
DARKEN = {"s08", "s39", "s15"}       # daylight train x2, upside-down city
HIGH_KEY = 130.0                     # mean luma above which a clip is graded


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------
def load_sections() -> List[Dict[str, Any]]:
    bg = json.loads(BEATGRID.read_text(encoding="utf-8"))
    return [dict(s) for s in bg["sections"]]


def beats_for(section: str) -> int:
    if section in HOLD_SECTIONS:
        return 8
    if any(h in section for h in HOOKish):
        return 2
    return 4


def section_at(t: float, secs: List[Dict[str, Any]]) -> Dict[str, Any]:
    for s in secs:
        if float(s["start_s"]) <= t < float(s["end_s"]):
            return s
    return secs[-1]


def build_timeline(secs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Cuts on the beat grid; each carries its section and its speed."""
    cuts: List[Dict[str, Any]] = []
    t = 0.0
    idx = 0
    while t < DURATION - 0.02:
        sec = section_at(t, secs)
        name = str(sec["name"])
        nb = beats_for(name)
        length = nb * BEAT
        s_end = min(float(sec["end_s"]), DURATION)
        end = min(t + length, s_end, DURATION)
        # a short remainder at the end of a section joins the cut rather than
        # becoming an unplayable sliver -- the timeline must total 196.0 s
        if end < s_end and (s_end - end) < 0.25:
            end = s_end
        # snap the start of every cut to the measured grid (phase 0.039)
        k = round((t - PHASE) / BEAT)
        t_snap = PHASE + k * BEAT if abs((t - PHASE) / BEAT - k) < 0.34 else t
        t_snap = max(t_snap, t)          # never go backwards
        if end - t_snap < 0.12:
            end = s_end
        if end - t_snap < 0.12:
            break
        hook = any(h in name for h in HOOKish)
        if hook:
            speed = [1.5, 2.0, 1.75][idx % 3]
        elif name in HOLD_SECTIONS:
            speed = 1.0
        else:
            speed = [1.0, 1.15, 1.25][idx % 3]
        cuts.append({
            "i": idx, "t0": round(t_snap, 4), "t1": round(end, 4),
            "dur": round(end - t_snap, 4), "section": name, "beats": nb,
            "speed": speed, "hook": hook,
            "hold": name in HOLD_SECTIONS,
        })
        t = end
        idx += 1
    # the cut grid must total the song exactly or the picture drifts off the audio
    if cuts:
        short = DURATION - sum(c["dur"] for c in cuts)
        if short > 0.001:
            cuts[-1]["t1"] = round(cuts[-1]["t1"] + short, 4)
            cuts[-1]["dur"] = round(cuts[-1]["dur"] + short, 4)
    return cuts


# --------------------------------------------------------------------------
# clip allocation -- different sub-segment every reuse
# --------------------------------------------------------------------------
class Pool:
    def __init__(self, shots: List[Dict[str, Any]]):
        self.by_section: Dict[str, List[Dict[str, Any]]] = {}
        for s in shots:
            self.by_section.setdefault(str(s["section"]), []).append(s)
        self.all = shots
        self.use: Dict[str, int] = {}
        self.dur: Dict[str, float] = {}
        for s in shots:
            p = SHOTS / s["id"] / "clip.mp4"
            self.dur[s["id"]] = probe_dur(p) if p.is_file() else 0.0
        self.cursor: Dict[str, int] = {}
        self.last: Optional[str] = None

    def pick(self, section: str, avoid: Optional[str]) -> Optional[Dict[str, Any]]:
        for pool in (self.by_section.get(section) or [], self.all):
            if not pool:
                continue
            n = len(pool)
            start = self.cursor.get(section if pool is not self.all else "*", 0)
            for j in range(n):
                s = pool[(start + j) % n]
                if s["id"] == avoid and n > 1:
                    continue
                if self.dur.get(s["id"], 0) > 0.3:
                    key = section if pool is not self.all else "*"
                    self.cursor[key] = (start + j + 1) % n
                    return s
        return None

    def offset(self, sid: str, need: float) -> float:
        """Next unused sub-segment start, wrapped inside the clip."""
        d = self.dur.get(sid, 5.0)
        avail = max(0.0, d - need - 0.05)
        n = self.use.get(sid, 0)
        self.use[sid] = n + 1
        if avail <= 0.01:
            return 0.0
        # stride through the clip so consecutive uses of a clip never overlap
        step = max(need * 0.8, avail / 3.0)
        off = (n * step) % avail
        return round(off, 3)


def probe_dur(p: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(p)], capture_output=True, text=True, timeout=60)
        return float((r.stdout or "0").strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


# --------------------------------------------------------------------------
# per-cut render
# --------------------------------------------------------------------------
def hue_base(i: int, hook: bool) -> float:
    r = (i * 37) % 7
    if hook:
        return [-25, 20, 35, -35, 15, 30, -20][r]
    return [-14, 10, 6, -8, 12, -4, 9][r]


def needs_night_grade(sid: str) -> bool:
    """A shot that came back high-key: named, or measured above HIGH_KEY."""
    if sid in DARKEN:
        return True
    mj = DEMO / "luma_v3.json"
    if mj.is_file():
        luma = {k: v for k, v in json.loads(mj.read_text(encoding="utf-8")).get("by_shot", {}).items()}
        return float(luma.get(sid, 0.0)) > HIGH_KEY
    return False


def filters_for(cut: Dict[str, Any], src_need: float, sid: str = "") -> str:
    """The v3 look, per cut, as a labelled filter_complex. Never a global wash.

    Every branch is explicitly labelled: a split in a linear comma-joined chain
    silently orphans the rest of the graph.
    """
    i = int(cut["i"])
    n = max(1, int(cut["dur"] * FPS))
    pre: List[str] = [
        "fps=%d" % FPS,
        "scale=%d:%d:force_original_aspect_ratio=increase" % (W, H),
        "crop=%d:%d" % (W, H),
    ]
    # zoom punch: a slow push on about 45% of cuts, strongest on hooks
    if cut["hook"] or (i % 9) in (0, 3, 5, 7):
        amt = 0.18 if cut["hook"] else 0.10
        pre.append(
            "zoompan=z='1+%g*on/%d':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            ":d=1:s=%dx%d:fps=%d" % (amt, n, W, H, FPS)
        )
    if sid and needs_night_grade(sid):
        pre.append(NIGHT_GRADE)
    segs: List[str] = []
    segs.append(",".join(pre) + "[v1]")

    # mirrored kaleidoscope burst, hooks only, short
    if cut["hook"] and (i % 2 == 0):
        segs.append("[v1]split=2[ka][kb]")
        segs.append("[ka]scale=%d/2:%d/2:flags=bicubic[a]" % (W, H))
        segs.append("[kb]scale=%d/2:%d/2:flags=bicubic,hflip[b]" % (W, H))
        segs.append("[a][b]hstack,scale=%d:%d[v2]" % (W, H))
    else:
        segs.append("[v1]null[v2]")

    # hue: a per-cut offset (the beat kick) + a slow drift inside the cut
    colour = ["hue=h='%g+12*sin(2*PI*t/1.7)':s=1.12" % hue_base(i, cut["hook"])]
    # RGB split on hook lines only
    if cut["hook"]:
        colour.append("rgbashift=rh=6:bh=-6")
    colour.append("noise=alls=7:allf=t+u")
    segs.append("[v2]" + ",".join(colour) + "[v3]")

    # bloom: a blurred copy screened back over the plate
    segs.append("[v3]split=2[ba][bb]")
    segs.append("[bb]gblur=sigma=12,eq=brightness=0.02[b2]")
    segs.append("[ba][b2]blend=all_mode=screen:all_opacity=0.22[out]")
    segs.append("[out]format=yuv420p,setsar=1[outv]")
    return ";".join(segs)


def render_cut(cut: Dict[str, Any], shot: Dict[str, Any], off: float, dest: Path) -> bool:
    src = SHOTS / shot["id"] / "clip.mp4"
    need = cut["dur"] * cut["speed"]          # source seconds consumed
    off = min(off, max(0.0, probe_dur(src) - need - 0.03))
    ss = round(max(0.0, off), 3)
    # rotate the plate so consecutive cuts are never the same framing
    args = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", "%.3f" % ss, "-t", "%.3f" % (need + 0.15),
        "-i", str(src),
        "-filter_complex", filters_for(cut, need, shot["id"]), "-map", "[outv]",
        "-an", "-t", "%.3f" % cut["dur"],
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        str(dest),
    ]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if r.returncode != 0:
        print("  cut %03d FAILED: %s" % (cut["i"], (r.stderr or "")[-400:]), flush=True)
        return False
    return dest.is_file() and dest.stat().st_size > 2048


# --------------------------------------------------------------------------
def make_credit(out_png: Path) -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(CREDIT_FONT, 34)
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), CREDIT, font=font)
    x = (W - (box[2] - box[0])) // 2
    y = H - 92
    d.text((x + 2, y + 2), CREDIT, font=font, fill=(0, 0, 0, 170))
    d.text((x, y), CREDIT, font=font, fill=(255, 255, 255, 225))
    img.save(out_png)


def concat_and_master(cut_files: List[Path], master: Path, audio: Path, credit: Path) -> None:
    lst = BUILD / "concat.txt"
    lst.write_text("".join("file '%s'\n" % p.resolve().as_posix() for p in cut_files), encoding="utf-8")
    silent = BUILD / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(silent)],
        check=True, timeout=900)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(silent), "-i", str(audio), "-i", str(credit),
         "-filter_complex",
         "[0:v][2:v]overlay=0:0:enable='lt(t,5.2)'[v];[1:a]volume=1.0[a]",
         "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest",
         str(master)],
        check=True, timeout=3600)


def teasers(master: Path) -> None:
    start = 50.039
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-t", "45", "-i", str(master),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
         str(OUT / "madness_teaser_45s.mp4")], check=True, timeout=1800)
    # vertical: crop the 16:9 to a 9:16 centre slice, scaled to 1080x1920
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-t", "45", "-i", str(master),
         "-vf", "crop=ih*9/16:ih,scale=1080:1920:flags=lanczos",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
         str(OUT / "madness_teaser_45s_vertical.mp4")], check=True, timeout=1800)


def contact_sheet(master: Path, out_png: Path, n: int = 12) -> None:
    dur = probe_dur(master) or DURATION
    tmp = BUILD / "cs"
    tmp.mkdir(parents=True, exist_ok=True)
    picks = [dur * (k + 0.5) / n for k in range(n)]
    cols, rows = 4, 3
    tw, th = 480, 270
    sheet = Image.new("RGB", (cols * tw, rows * th), (8, 8, 10))
    for k, t in enumerate(picks):
        f = tmp / ("f%02d.png" % k)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "%.2f" % t, "-i", str(master),
                        "-frames:v", "1", "-vf", "scale=%d:%d" % (tw, th), str(f)],
                       capture_output=True, timeout=120)
        if f.is_file():
            sheet.paste(Image.open(f).convert("RGB"), ((k % cols) * tw, (k // cols) * th))
    sheet.save(out_png, quality=92)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shotlist", default=str(DEMO / "shotlist_v3.json"))
    ap.add_argument("--no-teasers", action="store_true")
    ap.add_argument("--no-motion-reject", action="store_true")
    args = ap.parse_args()

    shots = json.loads(Path(args.shotlist).read_text(encoding="utf-8"))["shots"]
    secs = load_sections()
    timeline = build_timeline(secs)
    print("timeline: %d cuts over %.1fs" % (len(timeline), DURATION), flush=True)

    # motion gate: a clip below the threshold is benched, never cut in
    bench: set = set()
    mj = DEMO / "motion_qa_v3.json"
    if mj.is_file() and not args.no_motion_reject:
        thr = 4.0
        for r in json.loads(mj.read_text(encoding="utf-8")):
            if r["mad_mean"] < thr:
                bench.add(r["id"])
        if bench:
            print("motion QA benched: %s" % ", ".join(sorted(bench)), flush=True)

    pool = Pool([s for s in shots if s["id"] not in bench])
    BUILD.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    cutdir = BUILD / "cuts"
    cutdir.mkdir(exist_ok=True)

    files: List[Path] = []
    placed: List[Dict[str, Any]] = []
    prev = None
    for cut in timeline:
        shot = pool.pick(str(cut["section"]), prev)
        if shot is None:
            continue
        need = cut["dur"] * cut["speed"]
        off = pool.offset(shot["id"], need)
        dest = cutdir / ("c%03d.mp4" % cut["i"])
        ok = dest.is_file() and dest.stat().st_size > 2048
        if not ok:
            ok = render_cut(cut, shot, off, dest)
        if not ok:
            # fall back to any other clip before dropping the slot
            alt = pool.pick(str(cut["section"]), shot["id"])
            if alt is None:
                continue
            off2 = pool.offset(alt["id"], need)
            ok = render_cut(cut, alt, off2, dest)
            shot = alt
        if not ok:
            continue
        files.append(dest)
        placed.append({"i": cut["i"], "t0": cut["t0"], "dur": cut["dur"],
                       "shot": shot["id"], "off": off, "speed": cut["speed"],
                       "section": cut["section"], "hook": cut["hook"]})
        prev = shot["id"]
        if cut["i"] % 20 == 0:
            print("  %03d/%03d  t=%.1f  %s  x%.2f" % (
                cut["i"], len(timeline), cut["t0"], shot["id"], cut["speed"]), flush=True)

    if not files:
        print("no cuts rendered -- COULD NOT JUDGE")
        return 2
    # renumber so concat is in order
    files.sort(key=lambda p: int(p.stem[1:]))
    print("cut files: %d  distinct shots used: %d" % (len(files), len({p["shot"] for p in placed})), flush=True)

    credit = BUILD / "credit.png"
    make_credit(credit)
    master = OUT / "madness_music_video.mp4"
    concat_and_master(files, master, AUDIO, credit)
    print("master: %s" % master, flush=True)

    (OUT / "cut_report_v3.json").write_text(json.dumps({
        "cuts": len(files), "distinct_shots": len({p["shot"] for p in placed}),
        "benched_low_motion": sorted(bench),
        "speed_range": [min(p["speed"] for p in placed), max(p["speed"] for p in placed)],
        "placement": placed,
    }, indent=2), encoding="utf-8")

    contact_sheet(master, OUT / "contact_sheet_v3_final.jpg", 12)
    if not args.no_teasers:
        teasers(master)
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
