#!/usr/bin/env python3
"""Cut "madness." from the rendered shots.

Reads demo/shotlist.json (start/end on the song's beat grid, one transition per
shot) and demo/shots/<id>/clip.mp4, then:

  1. builds one graded segment per shot, retimed to sit exactly on its
     start_s..end_s slot (never a freeze frame: the clip is stretched or sped
     with setpts, and a shot whose clip is missing falls back to a slow push on
     its keyframe, and says so);
  2. joins them on the grid -- hard cuts, 6-frame white/amber flashes, 0.2 s
     dissolves, 0.2 s whip (hblur) pushes;
  3. muxes the song, burns the lower-third credit, writes the master and both
     45 s social teasers.

    python demo/cut.py                 # master + teasers
    python demo/cut.py --no-teasers
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEMO = Path(__file__).resolve().parent
SHOTS = DEMO / "shots"
OUT = DEMO / "out"
BUILD = DEMO / ".cut"
AUDIO = DEMO / "music" / "madness.wav"
BEATGRID = DEMO / "music" / "beatgrid.json"
SECTIONS = DEMO / "music" / "sections.json"

W, H, FPS = 1920, 1080, 30
DISSOLVE_S = 0.2          # 6 frames
WHIP_S = 0.2
FLASH_S = 0.07            # 2 frames of white resolving into the shot
XFADE = {"dissolve": "fade", "whip": "hblur"}
CREDIT = (
    "madness.  \u2014  directed in Aither Cinema Studio  \u00b7  rendered on Comfy Cloud  \u00b7  "
    "open weights (Wan 2.2)"
)
TEASER_START = 50.039      # the hook downbeat
TEASER_LEN = 45.0

# The beat grid, read from the measured song rather than assumed.  144 BPM,
# 4/4 -> a bar every 1.6667 s, downbeats on 0.039 + n*1.6667 (all 119 measured
# downbeats fit that phase).  HOOKS are the max-chaos windows.
BAR_S = 60.0 / 144.0 * 4.0
PHASE_S = 0.039


def load_grid() -> Dict[str, Any]:
    """Downbeat phase, bar length and the section spans from the measured grid."""
    bg = json.loads(BEATGRID.read_text(encoding="utf-8"))
    bpm = float(bg.get("bpm") or 144)
    bar = 60.0 / bpm * 4.0
    downs = [float(x) for x in (bg.get("downbeats_s") or [])]
    phase = downs[0] if downs else 0.0
    spans = []
    sec = json.loads(SECTIONS.read_text(encoding="utf-8"))
    for s in sec.get("sections") or []:
        name = str(s.get("name") or "")
        kind = str(s.get("kind") or "")
        a, b = float(s["start_s"]), float(s["end_s"])
        # hooks and the two drops that carry the biggest images
        if kind == "hook" or "hook" in name or name in ("p2-verse-2", "p3-outro-2"):
            spans.append((a, b))
    # merge touching spans
    spans.sort()
    merged: List[Tuple[float, float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 0.05:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    bounds = sorted({round(float(s["start_s"]), 3) for s in sec.get("sections") or []})
    return {"bpm": bpm, "bar_s": bar, "phase_s": phase, "hooks": merged, "bounds": bounds,
            "downbeats": downs}


def sh(cmd: List[str], what: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        sys.stderr.write("\n[%s] ffmpeg failed (exit %s)\n%s\n" % (what, proc.returncode, proc.stderr[-3000:]))
        raise SystemExit(1)


def probe(path: Path) -> Dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,nb_frames", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    doc = json.loads(out.stdout or "{}")
    st = (doc.get("streams") or [{}])[0]
    return {
        "duration_s": float((doc.get("format") or {}).get("duration") or 0.0),
        "width": int(st.get("width") or 0),
        "height": int(st.get("height") or 0),
    }


def grade_chain() -> str:
    """Per-segment pass.  Deliberately light: the look (hue drift, bloom, grain,
    kaleidoscope, glitch) is one beat-synced pass over the whole cut, in
    psy_filter(), so 54 segments are not each paying for gblur and grain."""
    return (
        "scale=%d:%d:flags=lanczos:force_original_aspect_ratio=decrease,"
        "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%d,"
        "eq=saturation=1.10"
    ) % (W, H, W, H, FPS)


def psy_filter(grid: Dict[str, Any]) -> str:
    """The 2026-10-30 psychedelic pass, beat-synced from the measured grid.

    Restraint in the verses, max chaos in the hooks: a hue rotation that drifts
    through the song and kicks on every downbeat, chromatic-aberration bursts on
    the hook bars, a zoom punch on each downbeat, a mirror-tile kaleidoscope over
    the hooks, frame-blend (datamosh-ish) bursts on the section boundaries, then
    bloom, grain and a heavier neon grade.  Input [0:v] -> [pv].
    """
    phase, bar = float(grid["phase_s"]), float(grid["bar_s"])
    # bar position in seconds, 0 at each downbeat
    p = "mod(t-%.4f,%.5f)" % (phase, bar)
    po = "mod(on/%.1f-%.4f,%.5f)" % (FPS, phase, bar)
    kick = "max(0,1-%s/0.22)" % p                 # 1 on the downbeat -> 0 by 0.22 s
    kick_o = "max(0,1-%s/0.22)" % po
    hook = "+".join("between(t,%.3f,%.3f)" % (a, b) for a, b in grid["hooks"])
    hook_o = "+".join("between(on/%.1f,%.3f,%.3f)" % (FPS, a, b) for a, b in grid["hooks"])
    # one short frame-blend burst on every section boundary
    bursts = "+".join("between(t,%.3f,%.3f)" % (b, b + 0.17) for b in grid["bounds"][1:])
    hue = (
        "h='12*sin(2*PI*t/19)+6*sin(2*PI*t/5.5)+22*%s*(%s)':s=1.06" % (kick, hook)
    )
    zoom = "1+0.018*%s+0.020*%s*(%s)" % (kick_o, kick_o, hook_o)
    return ";".join([
        # neon base grade: split-tone curves (magenta shadows, cyan mids, hot
        # highlights) so even the desaturated v1 shots read in colour
        "[0:v]scale=%d:%d:flags=lanczos,setsar=1,fps=%d,"
        "eq=saturation=1.60:contrast=1.14:gamma=0.98:brightness=-0.015,"
        "curves=r='0/0 0.22/0.30 0.55/0.66 1/1'"
        ":g='0/0.02 0.22/0.14 0.55/0.42 1/0.96'"
        ":b='0/0.07 0.22/0.34 0.55/0.62 1/1',"
        "colorbalance=rs=0.04:bs=0.03[gr]" % (W, H, FPS),
        # hue drifts through the song, kicks on the downbeat, swings hard on hooks
        "[gr]hue=%s[hue]" % hue,
        # zoom punch on every downbeat; setpts restores the timeline that
        # zoompan resets (it counts output frames from zero)
        "[hue]zoompan=z='%s':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        ":d=1:s=%dx%d:fps=%d,setpts=N/(%d*TB)[zp]" % (zoom, W, H, FPS, FPS),
        # bloom: blurred copy screened back over the frame
        "[zp]split[bg][bb]",
        "[bb]gblur=sigma=24[bl]",
        "[bg][bl]blend=all_mode=screen:all_opacity=0.26[bm0]",
        # animated violet -> cyan light wash: colour on footage that has none
        "gradients=s=%dx%d:r=%d:c0=0x500070:c1=0x00E676:speed=0.018:n=2:d=197[wash0]"
        % (W, H, FPS),
        "[wash0]format=yuv420p[wash]",
        "[bm0][wash]blend=all_mode=screen:all_opacity=0.22[bm]",
        # mirror-tile kaleidoscope, on the hooks only
        "[bm]split[km][kc]",
        "[kc]crop=%d:%d:%d:%d,split=4[k0][k1][k2][k3]" % (W // 2, H // 2, W // 4, H // 4),
        "[k1]hflip[f1]",
        "[k2]vflip[f2]",
        "[k3]hflip,vflip[f3]",
        "[k0][f1][f2][f3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0,"
        "format=rgba,colorchannelmixer=aa=0.45[kt]",
        "[km][kt]overlay=0:0:enable='%s'[ko]" % hook,
        # a rainbow pseudocolour wash over the hooks: max chaos where the song is
        "[ko]split[pm][pp]",
        "[pp]pseudocolor=preset=turbo[ppc]",
        "[pm][ppc]blend=all_mode=overlay:all_opacity=0.30:enable='%s'[pw]" % hook,
        # datamosh-ish frame blend on the section boundaries
        "[pw]tmix=frames=5:enable='%s'[gl]" % bursts,
        # chromatic aberration on the hook bars
        "[gl]rgbashift=rh=7:bh=-7:rv=2:bv=-2:enable='%s*(%s)'[ca]" % (kick, hook),
        # grain, vignette, a last sharpening
        "[ca]noise=alls=8:allf=t+u,vignette=PI/4.9,unsharp=5:5:0.30:5:5:0.0[pv]",
    ])


def build_segment(shot: Dict[str, Any], incoming: str, extended: float) -> Tuple[Path, str]:
    """One shot -> one exactly-timed, graded segment. Returns (path, source kind)."""
    sid = shot["id"]
    target = BUILD / ("seg_%s.mp4" % sid)
    slot = float(shot["end_s"]) - float(shot["start_s"])
    dur = slot + extended
    clip = SHOTS / sid / "clip.mp4"
    key = SHOTS / sid / "key.png"
    chain = grade_chain()
    flash = ""
    if incoming == "beat-flash":
        flash = ",fade=t=in:st=0:d=%.3f:color=white" % FLASH_S

    if clip.is_file() and probe(clip)["duration_s"] > 0.2:
        src_dur = probe(clip)["duration_s"]
        factor = dur / src_dur
        vf = "setpts=%.6f*(PTS-STARTPTS),%s%s" % (factor, chain, flash)
        sh(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-vf", vf, "-t", "%.3f" % dur,
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
            "-pix_fmt", "yuv420p", str(target)], sid)
        return target, "clip"

    if key.is_file():
        # no clip: a slow push on the keyframe rather than a dead freeze
        frames = max(2, int(round(dur * FPS)))
        # zoompan holds each input frame for `d` output frames -- with a single
        # still that d IS the clip length (d=1 silently truncates the shot).
        vf = (
            "scale=%d:-1,zoompan=z='min(1.0+0.00035*on,1.10)':"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=%d:s=%dx%d:fps=%d,"
            "setsar=1%s" % (int(W * 1.3), frames, W, H, FPS, flash)
        )
        sh(["ffmpeg", "-y", "-v", "error", "-i", str(key), "-vf", vf,
            "-t", "%.3f" % dur, "-frames:v", str(frames), "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
            "-pix_fmt", "yuv420p", str(target)], sid)
        return target, "keyframe-still"

    return target, "missing"


def concat(paths: List[Path], target: Path) -> None:
    lst = BUILD / (target.stem + ".txt")
    lst.write_text("".join("file '%s'\n" % p.as_posix() for p in paths), encoding="utf-8")
    sh(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c", "copy", str(target)], target.stem)


def make_credit(path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    font = None
    for cand in (r"C:/Windows/Fonts/arial.ttf", r"C:/Windows/Fonts/segoeui.ttf", r"C:/Windows/Fonts/calibri.ttf"):
        try:
            font = ImageFont.truetype(cand, 26)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), CREDIT, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    x, y = 72, H - 96
    d.rectangle([x - 14, y - 10, x + tw + 14, y + th + 12], fill=(0, 0, 0, 120))
    d.text((x, y), CREDIT, font=font, fill=(255, 255, 255, 225))
    img.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shotlist", default=str(DEMO / "shotlist.json"))
    ap.add_argument("--no-teasers", action="store_true")
    ap.add_argument("--no-psy", action="store_true", help="skip the psychedelic pass")
    ap.add_argument("--keep-build", action="store_true")
    args = ap.parse_args()

    doc = json.loads(Path(args.shotlist).read_text(encoding="utf-8"))
    shots = sorted(doc["shots"], key=lambda s: float(s["start_s"]))
    OUT.mkdir(parents=True, exist_ok=True)
    if BUILD.exists() and not args.keep_build:
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True, exist_ok=True)

    # ---- 1. segments -------------------------------------------------------
    segs: Dict[str, Path] = {}
    kinds: Dict[str, str] = {}
    missing: List[str] = []
    for i, shot in enumerate(shots):
        incoming = shots[i - 1]["transition"] if i else "cut"
        outgoing = shot["transition"]
        extended = DISSOLVE_S if outgoing in XFADE else 0.0
        path, kind = build_segment(shot, incoming, extended)
        if kind == "missing":
            missing.append(shot["id"])
            continue
        # every segment must be exactly its slot: a short one silently slides
        # the whole cut off the beat grid (measured once, hence this check).
        want = float(shot["end_s"]) - float(shot["start_s"]) + extended
        got = probe(path)["duration_s"]
        if abs(got - want) > 1.5 / FPS:
            print("WARNING: %s is %.3fs, wanted %.3fs" % (shot["id"], got, want))
        segs[shot["id"]] = path
        kinds[shot["id"]] = kind
    if missing:
        print("WARNING: no media for %s -- the timeline will be short there" % ", ".join(missing))

    # ---- 2. assemble -------------------------------------------------------
    runs: List[Tuple[List[str], Optional[str]]] = []   # (shot ids, transition out of the run)
    cur: List[str] = []
    for shot in shots:
        if shot["id"] not in segs:
            continue
        cur.append(shot["id"])
        if shot["transition"] in XFADE:
            runs.append((cur, shot["transition"]))
            cur = []
    if cur:
        runs.append((cur, None))

    run_files: List[Path] = []
    for n, (ids, _t) in enumerate(runs):
        if len(ids) == 1:
            run_files.append(segs[ids[0]])
        else:
            target = BUILD / ("run_%02d.mp4" % n)
            concat([segs[i] for i in ids], target)
            run_files.append(target)

    assembled = BUILD / "assembled.mp4"
    if len(run_files) == 1:
        shutil.copyfile(run_files[0], assembled)
    else:
        durs = [probe(p)["duration_s"] for p in run_files]
        inputs: List[str] = []
        for p in run_files:
            inputs += ["-i", str(p)]
        parts: List[str] = []
        prev = "0:v"
        elapsed = durs[0]
        for k in range(1, len(run_files)):
            trans = XFADE.get(runs[k - 1][1] or "dissolve", "fade")
            off = elapsed - DISSOLVE_S
            out = "v%d" % k
            parts.append(
                "[%s][%d:v]xfade=transition=%s:duration=%.3f:offset=%.3f[%s]"
                % (prev, k, trans, DISSOLVE_S, off, out)
            )
            prev = out
            elapsed = elapsed + durs[k] - DISSOLVE_S
        sh(["ffmpeg", "-y", "-v", "error"] + inputs +
           ["-filter_complex", ";".join(parts), "-map", "[%s]" % prev,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
            "-pix_fmt", "yuv420p", str(assembled)], "assemble")

    # ---- 3. master: psychedelic pass + credit + song ------------------------
    credit = BUILD / "credit.png"
    make_credit(credit)
    master = OUT / "madness_music_video.mp4"
    grid = load_grid()
    psy = "" if args.no_psy else psy_filter(grid) + ";"
    sh(["ffmpeg", "-y", "-v", "error", "-i", str(assembled), "-i", str(credit),
        "-i", str(AUDIO),
        "-filter_complex",
        psy + "[%s][1:v]overlay=0:0:enable='between(t,0.7,7.5)'[v]"
        % ("pv" if psy else "0:v"),
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
        str(master)], "master")

    # ---- 4. teasers --------------------------------------------------------
    teaser = OUT / "madness_teaser_45s.mp4"
    vertical = OUT / "madness_teaser_45s_vertical.mp4"
    if not args.no_teasers:
        sh(["ffmpeg", "-y", "-v", "error", "-ss", "%.3f" % TEASER_START, "-t", "%.3f" % TEASER_LEN,
            "-i", str(master),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(teaser)], "teaser")
        crop_w = int(round(H * 9 / 16 / 2)) * 2
        sh(["ffmpeg", "-y", "-v", "error", "-i", str(teaser),
            "-vf", "crop=%d:%d:(iw-%d)/2:0,scale=1080:1920:flags=lanczos,setsar=1"
            % (crop_w, H, crop_w),
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(vertical)], "vertical")

    report = {
        "master": str(master), "master_probe": probe(master),
        "teaser": str(teaser), "teaser_probe": probe(teaser) if teaser.is_file() else None,
        "vertical": str(vertical), "vertical_probe": probe(vertical) if vertical.is_file() else None,
        "shots_in_cut": len(segs), "shots_missing": missing,
        "still_fallbacks": sorted([k for k, v in kinds.items() if v == "keyframe-still"]),
    }
    (OUT / "cut_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not args.keep_build:
        shutil.rmtree(BUILD, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
