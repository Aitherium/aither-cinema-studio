#!/usr/bin/env python3
"""Build the contest demo video for Aither Cinema Studio (<2 minutes).

    python demo/make_demo.py                 # full build
    python demo/make_demo.py --skip-vo       # reuse synthesised narration
    python demo/make_demo.py --only cards    # rebuild graphics

Inputs   demo/out/madness_music_video.mp4   (the film)
         demo/music/madness.wav             (its score, for the bed)
         demo/capture/0*.webm               (the build in action)
         demo/capture/claude-code-mcp.log   (an agent driving the same tools)
Output   demo/out/aither_cinema_studio_demo.mp4  1920x1080 h264+aac, <120 s

Beats (see SUBMISSION.md §12): cold open -> build in action -> how it is built +
why it is innovative -> payoff montage + end card. Narration is local Kokoro ONNX
(am_michael); the score is ducked under it and the whole mix is mastered to
-14 LUFS. Graphics are drawn with PIL, text is burned with ffmpeg drawtext.

Every number that the edit depends on (segment lengths, narration slots) is
computed here, not typed, so the running time can be read off the build log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.path.join(ROOT, ".demo_work")
SEG = os.path.join(WORK, "seg")
CARD = os.path.join(WORK, "card")
CAP = os.path.join(WORK, "cap")
FONT_DIR = os.path.join(WORK, "fonts")
OUT = os.path.join(ROOT, "demo", "out", "aither_cinema_studio_demo.mp4")

MV = os.path.join(ROOT, "demo", "out", "madness_music_video.mp4")
SONG = os.path.join(ROOT, "demo", "music", "madness.wav")
CAPTURES = os.path.join(ROOT, "demo", "capture")
MCP_LOG = os.path.join(CAPTURES, "claude-code-mcp.log")

KOKORO_MODEL = "D:/Caches/aither-models/kokoro/kokoro-v1.0.onnx"
KOKORO_VOICES = "D:/Caches/aither-models/kokoro/voices-v1.0.bin"

W, H, FPS = 1920, 1080, 30
VO_SPEED = 1.05          # brisk but still calm
MAX_DURATION = 119.0     # hard rule: the entry says <2 minutes

# palette: dark film set, amber accent
BG = (11, 11, 13)
PANEL = (18, 19, 23)
LINE = (43, 45, 52)
AMBER = (232, 163, 61)
AMBER_LIGHT = (255, 199, 128)
TEXT = (241, 242, 245)
MUTED = (141, 147, 157)

sys.path.insert(0, HERE)
from vo_lines import LINES, VOICES  # noqa: E402


def sh(cmd, **kw):
    """Run a command, echo it, fail loudly."""
    if isinstance(cmd, list):
        shown = " ".join(cmd)
    else:
        shown = cmd
    print("  $", shown if len(shown) < 400 else shown[:400] + " …", flush=True)
    return subprocess.run(cmd, cwd=ROOT, shell=isinstance(cmd, str), check=True, **kw)


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], cwd=ROOT, capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


# --------------------------------------------------------------------------- VO
def synth_vo(force=False):
    """Synthesise one WAV per line with local Kokoro; returns {key: duration}."""
    from kokoro_onnx import Kokoro
    import numpy as np

    os.makedirs(os.path.join(WORK, "vo"), exist_ok=True)
    k = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
    durs = {}
    for key, _kicker, spoken, _cap in LINES:
        path = os.path.join(WORK, "vo", key + ".wav")
        stamp = os.path.join(WORK, "vo", key + ".txt")
        if not force and os.path.exists(path) and os.path.exists(stamp):
            if open(stamp, encoding="utf-8").read() == f"{spoken}|{VO_SPEED}":
                durs[key] = probe_duration(path)
                continue
        samples, sr = k.create(spoken, voice=VOICES, speed=VO_SPEED, lang="en-us")
        # -1.5 dB headroom, tiny fade at each end so the cut never clicks
        a = np.clip(samples, -1.0, 1.0) * 0.84
        n = len(a)
        f = min(int(0.008 * sr), n // 4)
        a[:f] *= np.linspace(0, 1, f)
        a[-f:] *= np.linspace(1, 0, f)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((a * 32767).astype("<i2").tobytes())
        open(stamp, "w", encoding="utf-8").write(f"{spoken}|{VO_SPEED}")
        durs[key] = len(samples) / sr
        print(f"  vo {key:9s} {durs[key]:6.2f}s")
    return durs


# ------------------------------------------------------------------------- cards
def _fonts():
    os.makedirs(FONT_DIR, exist_ok=True)
    for src, dst in [
        ("C:/Windows/Fonts/segoeui.ttf", "segoeui.ttf"),
        ("C:/Windows/Fonts/seguisb.ttf", "seguisb.ttf"),
        ("C:/Windows/Fonts/segoeuil.ttf", "segoeuil.ttf"),
        ("C:/Windows/Fonts/consola.ttf", "consola.ttf"),
        ("C:/Windows/Fonts/consolab.ttf", "consolab.ttf"),
    ]:
        dstp = os.path.join(FONT_DIR, dst)
        if not os.path.exists(dstp):
            shutil.copyfile(src, dstp)


def _font(name, size):
    from PIL import ImageFont
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)


def _tracked(draw, xy, text, font, fill, tracking=0.0, anchor_left=True):
    """Draw letter-spaced text (PIL has no tracking)."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x


def _tracked_width(draw, text, font, tracking=0.0):
    return sum(draw.textlength(c, font=font) for c in text) + tracking * max(0, len(text) - 1)


def _kicker(draw, xy, text, size=26, color=AMBER, tracking=5.0):
    _tracked(draw, xy, text.upper(), _font("seguisb.ttf", size), color, tracking)


def card_diagram(path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _kicker(d, (110, 96), "how it is built", 27)
    d.text((110, 140), "One tool surface, three ways in", font=_font("segoeuil.ttf", 58), fill=TEXT)
    d.line([(110, 236), (1810, 236)], fill=LINE, width=2)

    boxes = [
        (110, "BROWSER TAB", ["React + TypeScript studio", "every control is a WebMCP tool",
                              "11 verbs: plan · set-camera ·", "render · re-shoot · grade · cut"]),
        (700, "FASTAPI BACKEND", ["cinema/ app, httpx client", "plan compiles the rig to a prompt",
                                  "/prompt → /history → /view", "pytest against a fake Comfy"]),
        (1290, "COMFY API", ["Cloud or your own ComfyUI", "Wan 2.2 Fun Camera +",
                             "lightx2v 4-step · LTX-2 · SDXL", "ComfyUI-CinemaRig node pack"]),
    ]
    bw, bh, by = 520, 300, 300
    for x, head, lines in boxes:
        d.rounded_rectangle([x, by, x + bw, by + bh], radius=14, fill=PANEL, outline=LINE, width=2)
        d.rectangle([x, by, x + 6, by + bh], fill=AMBER)
        _tracked(d, (x + 34, by + 26), head, _font("seguisb.ttf", 27), AMBER, 3.0)
        y = by + 82
        for ln in lines:
            d.text((x + 34, y), ln, font=_font("segoeui.ttf", 27), fill=TEXT)
            y += 44
    # arrows between the boxes
    for x0, x1, label in [(636, 692, "WebMCP"), (1226, 1282, "HTTP")]:
        cy = by + bh // 2
        d.line([(x0, cy), (x1 - 16, cy)], fill=AMBER, width=3)
        d.polygon([(x1, cy), (x1 - 18, cy - 10), (x1 - 18, cy + 10)], fill=AMBER)
        d.text((x0 - 26, cy - 44), label, font=_font("segoeui.ttf", 20), fill=MUTED)

    chips = ["ComfyUI-CinemaRig node pack", "cinema-mcp · 10 MCP tools",
             "Claude Code skill", "awdk agent pack", "MIT · no account"]
    x = 110
    for c in chips:
        f = _font("segoeui.ttf", 25)
        tw = d.textlength(c, font=f)
        d.rounded_rectangle([x, 660, x + tw + 44, 660 + 56], radius=28, fill=PANEL, outline=LINE, width=2)
        d.text((x + 22, 674), c, font=f, fill=TEXT)
        x += tw + 44 + 18

    d.line([(110, 736), (1810, 736)], fill=LINE, width=2)
    d.text((110, 762), "Open weights only: Wan 2.2 (Apache-2.0), SDXL RealVisXL, LTX-2. "
                       "Nothing proprietary in the path.",
           font=_font("segoeui.ttf", 28), fill=TEXT)
    d.text((110, 806), "ComfyUI is a GPL-3.0 server we call over HTTP — never imported, never linked, "
                       "so the MIT licence stands.",
           font=_font("segoeui.ttf", 28), fill=MUTED)
    d.text((110, 878), "github.com/Aitherium/aither-cinema-studio", font=_font("seguisb.ttf", 30), fill=AMBER)
    img.save(path)


def card_why(index, title, body, path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    _kicker(d, (110, 96), f"why it is innovative — {index} / 3", 27)
    d.line([(110, 150), (1810, 150)], fill=LINE, width=2)
    d.text((110, 300), title, font=_font("seguisb.ttf", 92), fill=TEXT)
    y = 440
    for ln in textwrap.wrap(body, 44):
        d.text((110, y), ln, font=_font("segoeuil.ttf", 52), fill=AMBER_LIGHT)
        y += 74
    d.text((110, 900), "#OpenSourceHiggsfield", font=_font("seguisb.ttf", 28), fill=MUTED)
    img.save(path)


def card_terminal(path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([90, 70, 1830, 1010], radius=14, fill=(8, 9, 11), outline=LINE, width=2)
    d.rounded_rectangle([90, 70, 1830, 126], radius=14, fill=(20, 21, 25))
    d.rectangle([90, 110, 1830, 126], fill=(20, 21, 25))
    for i, c in enumerate([(224, 108, 98), (224, 190, 98), (120, 190, 120)]):
        d.ellipse([120 + i * 30, 90, 136 + i * 30, 106], fill=c)
    d.text((240, 86), "claude code  —  cinema-mcp  —  aither-cinema-studio",
           font=_font("consola.ttf", 24), fill=MUTED)

    raw = open(MCP_LOG, encoding="utf-8", errors="replace").read()
    keep = [ln.rstrip() for ln in raw.splitlines()
            if ln.strip() and "claude.ai connectors" not in ln]
    body = []
    for ln in keep:
        ln = ln.replace("**", "")
        body.extend(textwrap.wrap(ln, 92) or [""])
    y = 156
    mono = _font("consola.ttf", 25)
    monob = _font("consolab.ttf", 25)
    for ln in body[:26]:
        if ln.startswith(">"):
            d.text((170, y), ln[:92], font=mono, fill=AMBER_LIGHT)
        elif re.match(r"^\d\)", ln) or ln.startswith("What the rig"):
            d.text((170, y), ln, font=monob, fill=AMBER)
        else:
            d.text((170, y), ln, font=mono, fill=TEXT)
        y += 33
    img.save(path)


def card_endcard(path):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # faint amber horizon
    for i in range(0, 60):
        a = int(10 * (1 - i / 60))
        d.line([(0, 700 + i), (W, 700 + i)], fill=(BG[0] + a, BG[1] + int(a * 0.6), BG[2]))
    _kicker(d, (154, 330), "aither cinema studio", 30, AMBER, 8.0)
    d.text((154, 380), "AITHER CINEMA STUDIO", font=_font("seguisb.ttf", 104), fill=TEXT)
    d.line([(154, 528), (154 + 260, 528)], fill=AMBER, width=6)
    d.text((154, 570), "An open-weights film set where AI agents direct the camera.",
           font=_font("segoeuil.ttf", 44), fill=MUTED)
    d.text((154, 700), "github.com/Aitherium/aither-cinema-studio",
           font=_font("seguisb.ttf", 46), fill=AMBER_LIGHT)
    d.text((154, 790), "#OpenSourceHiggsfield", font=_font("seguisb.ttf", 40), fill=TEXT)
    d.text((154, 880), "Wan 2.2 · LTX-2 · SDXL  ·  MIT  ·  no account, no credit meter",
           font=_font("segoeui.ttf", 28), fill=MUTED)
    img.save(path)


def card_title_overlay(path):
    """RGBA overlay for the cold open: scrim + title, fades in over the shot."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    px = img.load()
    for x in range(W):
        a = int(170 * max(0.0, 1 - x / 1500.0)) + 40
        for y in range(H):
            px[x, y] = (4, 4, 6, min(210, a + int(40 * max(0, 1 - abs(y - H * 0.55) / 420))))
    d = ImageDraw.Draw(img)
    _kicker(d, (120, 470), "comfy open source higgsfield entry", 24, AMBER_LIGHT, 4.0)
    d.text((118, 512), "AITHER CINEMA STUDIO", font=_font("seguisb.ttf", 92), fill=TEXT)
    d.line([(122, 646), (382, 646)], fill=AMBER, width=6)
    d.text((120, 690), "an open-weights film set where AI agents", font=_font("segoeuil.ttf", 44), fill=TEXT)
    d.text((120, 746), "direct the camera", font=_font("segoeuil.ttf", 44), fill=TEXT)
    img.save(path)


def build_cards():
    os.makedirs(CARD, exist_ok=True)
    _fonts()
    card_title_overlay(os.path.join(CARD, "title.png"))
    card_diagram(os.path.join(CARD, "diagram.png"))
    card_terminal(os.path.join(CARD, "terminal.png"))
    card_endcard(os.path.join(CARD, "end.png"))
    card_why(1, "Every control is a tool", "So any agent can direct. Eleven WebMCP verbs in the browser, ten MCP tools outside it.", os.path.join(CARD, "why1.png"))
    card_why(2, "A camera vocabulary, compiled", "21 moves, 7 lenses, 3 speeds, 10 framings compile to real camera conditioning, not prompt soup.", os.path.join(CARD, "why2.png"))
    card_why(3, "One tool surface", "Browser, MCP and node pack drive the same store. The human and the agent share the set.", os.path.join(CARD, "why3.png"))
    print("  cards ok")


# ---------------------------------------------------------------------- segments
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
       "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", "-y"]

UI_CHAIN = "fps=30,scale=1920:1080:flags=lanczos,setsar=1"


def _ramp(src, pieces, out, extra=""):
    """Cut [start,end) pieces from one source, play each at its own speed, concat."""
    parts, labels = [], []
    for i, (a, b, speed) in enumerate(pieces):
        parts.append(f"[0:v]trim=start={a}:end={b},setpts=(PTS-STARTPTS)/{speed}[v{i}]")
        labels.append(f"[v{i}]")
    chain = ";".join(parts) + ";"
    chain += "".join(labels) + f"concat=n={len(pieces)}:v=1:a=0,{UI_CHAIN}"
    if extra:
        chain += "," + extra
    chain += "[out]"
    sh(["ffmpeg", "-v", "error", "-y", "-i", src, "-filter_complex", chain,
        "-map", "[out]", *ENC, out])


def _still(card_png, dur, out, fade_in=0.35, pan=0.0):
    if pan:
        vf = (f"scale={int(W * (1 + pan))}:{int(H * (1 + pan))},"
              f"crop={W}:{H}:x='(in_w-out_w)*min(t/{dur:.3f},1)':y='(in_h-out_h)*min(t/{dur:.3f},1)',"
              f"setsar=1,fps=30,format=yuv420p")
    else:
        vf = f"scale={W}:{H},setsar=1,fps=30,format=yuv420p"
    if fade_in:
        vf += f",fade=t=in:st=0:d={fade_in}"
    sh(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", f"{dur:.3f}", "-i", card_png,
        "-vf", vf, *ENC, out])


def build_segments(vo):
    """Build every segment, return [(key, path, duration, vo_start_offset)]."""
    os.makedirs(SEG, exist_ok=True)
    segs = []

    def add(key, path, vo_lead=0.35):
        d = probe_duration(path)
        segs.append({"key": key, "path": path, "dur": d, "lead": vo_lead})
        print(f"  seg {key:9s} {d:6.2f}s")

    # 1 — cold open: the hardest shot in the film, then the title
    hook = os.path.join(SEG, "01_hook.mp4")
    sh(["ffmpeg", "-v", "error", "-y", "-i", MV,
        "-loop", "1", "-t", "8.0", "-i", os.path.join(CARD, "title.png"),
        "-filter_complex",
        # v3 cut: kaleidoscopic fractal tunnel -> burning atlas (38.6-41.6), then
        # eggshell butterflies -> flame corridor (130.2-133.8, slowed to 0.82).
        # Both are the film's highest-motion, face-free worlds.
        "[0:v]trim=start=38.60:end=41.60,setpts=(PTS-STARTPTS)/1.0,"
        "crop=1920:920:0:0,scale=-2:1080,crop=1920:1080,"
        "eq=contrast=1.04:saturation=1.06[v0];"
        "[0:v]trim=start=130.20:end=133.80,setpts=(PTS-STARTPTS)/0.82,"
        "crop=1920:920:0:0,scale=-2:1080,crop=1920:1080,"
        "eq=contrast=1.04:saturation=1.06[v1];"
        "[v0][v1]concat=n=2:v=1:a=0[base];"
        f"[base]fps=30,setsar=1[bg];"
        "[1:v]format=rgba,fade=t=in:st=3.3:d=0.9:alpha=1[t];"
        "[bg][t]overlay=0:0:format=auto,format=yuv420p[out]",
        "-map", "[out]", *ENC, hook])
    add("hook", hook, 0.25)

    # 2 — the set: settings, key, test connection green
    s = os.path.join(SEG, "02_settings.mp4")
    _ramp(os.path.join(CAPTURES, "01_settings.webm"),
          [(2.2, 15.04, 1.45)], s)
    add("settings", s)

    # 3 — an agent directs a shot: feed fills, then the render lands
    s = os.path.join(SEG, "03_agent.mp4")
    _ramp(os.path.join(CAPTURES, "02_agent_directs.webm"),
          [(9.5, 19.0, 1.75), (19.0, 42.68, 3.0)], s)
    add("agent", s, 0.45)

    # 4 — re-shoot from a new angle
    s = os.path.join(SEG, "04_reshoot.mp4")
    _ramp(os.path.join(CAPTURES, "03_reshoot.webm"),
          [(11.0, 18.0, 1.7), (18.0, 31.0, 3.2), (31.0, 39.44, 1.95)], s)
    add("reshoot", s, 0.45)

    # 5 — grade: monochrome -> teal & orange, straight through
    s = os.path.join(SEG, "05_grade.mp4")
    _ramp(os.path.join(CAPTURES, "04_grade.webm"), [(0.0, 10.4, 1.0)], s)
    add("grade", s, 0.3)

    # 6 — cut on the beat, then Claude Code driving cinema-mcp
    cut = os.path.join(SEG, "06a_cut.mp4")
    _ramp(os.path.join(CAPTURES, "05_timeline_cut.webm"), [(0.4, 6.4, 1.0)], cut)
    term = os.path.join(SEG, "06b_term.mp4")
    term_dur = max(7.0, vo["cut"] + 1.2)
    _still(os.path.join(CARD, "terminal.png"), term_dur, term, fade_in=0.3, pan=0.05)
    s = os.path.join(SEG, "06_cut.mp4")
    sh(["ffmpeg", "-v", "error", "-y", "-i", cut, "-i", term,
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[out]",
        "-map", "[out]", *ENC, s])
    add("cut", s, 0.4)

    # 7 — how it is built: one diagram card
    s = os.path.join(SEG, "07_diagram.mp4")
    d7 = max(13.0, vo["diagram"] + 1.3)
    _still(os.path.join(CARD, "diagram.png"), d7, s, fade_in=0.4)
    add("diagram", s, 0.45)

    # 8 — why it is innovative: three cards, crossfaded
    cards = [os.path.join(CARD, f"why{i}.png") for i in (1, 2, 3)]
    each = max(4.4, (vo["why"] + 1.3) / 3.0)
    s = os.path.join(SEG, "08_why.mp4")
    xf = 0.45
    sh(["ffmpeg", "-v", "error", "-y",
        "-loop", "1", "-t", f"{each:.3f}", "-i", cards[0],
        "-loop", "1", "-t", f"{each:.3f}", "-i", cards[1],
        "-loop", "1", "-t", f"{each:.3f}", "-i", cards[2],
        "-filter_complex",
        f"[0:v]scale=1920:1080,setsar=1,fps=30,format=yuv420p[a];"
        f"[1:v]scale=1920:1080,setsar=1,fps=30,format=yuv420p[b];"
        f"[2:v]scale=1920:1080,setsar=1,fps=30,format=yuv420p[c];"
        f"[a][b]xfade=transition=fade:duration={xf}:offset={each - xf:.3f}[ab];"
        f"[ab][c]xfade=transition=fade:duration={xf}:offset={2 * each - 2 * xf:.3f}[out]",
        "-map", "[out]", *ENC, s])
    add("why", s, 0.45)

    # 9 — payoff: three beats of the film, cut on downbeats. v3 worlds, picked
    # for motion and for the moments the lyric asks for: the dragon (25.4), the
    # black-wheat vortex into slamming doors (67.6), the train into the burst (154.5).
    s = os.path.join(SEG, "09_payoff.mp4")
    sh(["ffmpeg", "-v", "error", "-y", "-i", MV, "-filter_complex",
        "[0:v]trim=start=25.4:end=28.4,setpts=PTS-STARTPTS[a];"
        "[0:v]trim=start=67.6:end=70.6,setpts=PTS-STARTPTS[b];"
        "[0:v]trim=start=154.5:end=157.5,setpts=PTS-STARTPTS[c];"
        "[a][b][c]concat=n=3:v=1:a=0," + UI_CHAIN + "[out]",
        "-map", "[out]", *ENC, s])
    add("payoff", s, 0.4)

    # 10 — end card
    s = os.path.join(SEG, "10_end.mp4")
    _still(os.path.join(CARD, "end.png"), 8.0, s, fade_in=0.5)
    add("end", s, 0.5)

    return segs


# ------------------------------------------------------------------------ audio
def build_audio(segs, total, vo):
    """Narration on absolute time + a ducked score, mastered to -14 LUFS."""
    t0 = {}
    t = 0.0
    for s in segs:
        t0[s["key"]] = (t + s["lead"], t, t + s["dur"])
        t += s["dur"]

    # narration track: silence + each line at its slot (segments with no line are skipped)
    vo_paths = [(t0[k][0], os.path.join(WORK, "vo", k + ".wav")) for k in t0 if k in vo]
    inputs, filt, labels = [], [], []
    for i, (start, path) in enumerate(vo_paths):
        inputs += ["-i", path]
        ms = int(round(start * 1000))
        filt.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,"
                    f"adelay={ms}|{ms}[n{i}]")
        labels.append(f"[n{i}]")
    filt.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,"
                f"apad=whole_dur={total:.3f},atrim=0:{total:.3f},"
                f"aformat=channel_layouts=stereo[vo]")
    vo_track = os.path.join(WORK, "vo.wav")
    sh(["ffmpeg", "-v", "error", "-y", *inputs,
        "-filter_complex", ";".join(filt), "-map", "[vo]", "-ar", "48000", "-y", vo_track])

    # score: bed from the verse, then the hook lands exactly on the payoff montage
    mont = t0["payoff"][1]
    bed_off, hook_off = 26.7057, 50.039 + 1.6667 * 4
    bed_len = mont
    hook_len = total - mont
    music = os.path.join(WORK, "music.wav")
    sh(["ffmpeg", "-v", "error", "-y", "-i", SONG, "-filter_complex",
        f"[0:a]atrim=start={bed_off}:end={bed_off + bed_len},asetpts=PTS-STARTPTS,"
        f"aresample=48000,aformat=channel_layouts=stereo,volume=0.10[m1];"
        f"[0:a]atrim=start={hook_off}:end={hook_off + hook_len},asetpts=PTS-STARTPTS,"
        f"aresample=48000,aformat=channel_layouts=stereo,volume=0.62[m2];"
        f"[m1][m2]concat=n=2:v=0:a=1,afade=t=out:st={max(0.0, total - 2.2):.3f}:d=2.2[m]",
        "-map", "[m]", "-ar", "48000", music])

    # duck the score under the voice, mix, master
    mixed = os.path.join(WORK, "mix_raw.wav")
    sh(["ffmpeg", "-v", "error", "-y", "-i", music, "-i", vo_track,
        "-filter_complex",
        "[1:a]asplit=2[sc][vo];"
        "[0:a][sc]sidechaincompress=threshold=0.045:ratio=7:attack=8:release=420:"
        "makeup=1[duck];"
        "[duck][vo]amix=inputs=2:normalize=0:duration=longest,"
        f"atrim=0:{total:.3f},asetpts=PTS-STARTPTS[mix]",
        "-map", "[mix]", "-ar", "48000", mixed])

    # master to -14 LUFS in two passes (measure, then apply linearly)
    meas = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", mixed, "-af",
         "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
        cwd=ROOT, capture_output=True, text=True).stderr
    j = json.JSONDecoder().raw_decode(meas[meas.rindex("{"):])[0]
    mastered = os.path.join(WORK, "mix_master.wav")
    sh(["ffmpeg", "-v", "error", "-y", "-i", mixed, "-af",
        f"loudnorm=I=-14:TP=-1.5:LRA=11:linear=true:"
        f"measured_I={j['input_i']}:measured_TP={j['input_tp']}:"
        f"measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:"
        f"offset={j['target_offset']}", "-ar", "48000", mastered])
    print(f"  master: input {j['input_i']} LUFS -> target -14, TP ceiling -1.5 dBTP")
    return mastered, t0


# ---------------------------------------------------------------------- assemble
def wrap(text, width=76):
    return "\n".join(textwrap.wrap(text, width))


NO_CAPTION = {"hook"}  # the title card says it; a caption would stack on top of it


def build_captions(segs, t0, total, video_in, video_out):
    """Burn narration captions (kicker + line) at absolute times."""
    os.makedirs(CAP, exist_ok=True)
    f_bold = ".demo_work/fonts/seguisb.ttf"
    f_reg = ".demo_work/fonts/segoeui.ttf"
    filters, idx = [], 0
    for key, kicker, _sp, cap in LINES:
        if key not in t0 or key in NO_CAPTION:
            continue
        start, _seg0, seg1 = t0[key]
        end = min(seg1 - 0.12, start + probe_duration(os.path.join(WORK, "vo", key + ".wav")) + 1.15)
        # One drawtext per wrapped line, each from its own single-line textfile: ffmpeg
        # 8.0.1 drawtext paints a .notdef box for the "\n" at the end of a line in a
        # multi-line textfile (measured), so multi-line text is never used.
        body = wrap(cap, 74).split("\n")
        cy = 1076 - 14 - len(body) * 40
        ky = cy - 34
        kf = os.path.join(CAP, f"k{idx}.txt")
        open(kf, "w", encoding="utf-8", newline="\n").write(kicker.upper())
        filters.append(
            f"drawtext=fontfile={f_bold}:textfile=.demo_work/cap/k{idx}.txt:fontsize=22:fontcolor=0xE8A33D:"
            f"x=(w-text_w)/2:y={ky}:enable='between(t,{start:.2f},{end:.2f})':"
            f"shadowcolor=black@0.8:shadowx=0:shadowy=2")
        for j, ln in enumerate(body):
            cf = os.path.join(CAP, f"c{idx}_{j}.txt")
            open(cf, "w", encoding="utf-8", newline="\n").write(ln)
            filters.append(
                f"drawtext=fontfile={f_reg}:textfile=.demo_work/cap/c{idx}_{j}.txt:fontsize=30:fontcolor=0xF1F2F5:"
                f"x=(w-text_w)/2:y={cy + j * 40}:"
                f"enable='between(t,{start:.2f},{end:.2f})':"
                f"box=1:boxcolor=black@0.55:boxborderw=14:"
                f"shadowcolor=black@0.8:shadowx=0:shadowy=2")
        idx += 1
    script = os.path.join(WORK, "captions.filter")
    open(script, "w", encoding="utf-8").write("[0:v]" + ",".join(filters) + "[out]")
    sh(["ffmpeg", "-v", "error", "-y", "-i", video_in, "-filter_complex_script", script,
        "-map", "[out]", "-c:v", "libx264", "-preset", "medium", "-crf", "17",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", video_out])


def assemble(vo):
    print("[1/5] graphics")
    build_cards()
    print("[2/5] segments")
    segs = build_segments(vo)
    total = sum(s["dur"] for s in segs)
    print(f"  total {total:.2f}s")
    if total > MAX_DURATION:
        raise SystemExit(f"edit is {total:.1f}s — over the {MAX_DURATION}s cap")

    print("[3/5] concat")
    lst = os.path.join(WORK, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for s in segs:
            f.write(f"file '{os.path.relpath(s['path'], WORK).replace(os.sep, '/')}'\n")
    silent = os.path.join(WORK, "video_silent.mp4")
    sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
        "-c", "copy", silent])

    print("[4/5] audio")
    mastered, t0 = build_audio(segs, total, vo)

    print("[5/5] captions + mux")
    capped = os.path.join(WORK, "video_cap.mp4")
    build_captions(segs, t0, total, silent, capped)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    sh(["ffmpeg", "-v", "error", "-y", "-i", capped, "-i", mastered,
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-shortest", OUT])
    print("  wrote", OUT)
    return segs, t0


def qa():
    print("\n[QA]")
    d = probe_duration(OUT)
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                           "stream=codec_type,codec_name,width,height,r_frame_rate,channels,sample_rate",
                           "-of", "default=nw=1", OUT], capture_output=True, text=True).stdout
    print(f"  duration {d:.2f}s  (must be < 120)")
    print("  " + info.replace("\n", " | ").strip())
    sheet = os.path.join(ROOT, "demo", "demo_contact.jpg")
    sh(["ffmpeg", "-v", "error", "-y", "-i", OUT, "-vf",
        "select='not(mod(n\\,420))',scale=640:-1,tile=4x2", "-frames:v", "1", sheet])
    print("  contact sheet", sheet)


def existing_segs():
    """Segments already rendered in .demo_work/seg, in filename order (no re-render)."""
    leads = {"hook": 0.25, "settings": 0.35, "agent": 0.45, "reshoot": 0.45,
             "grade": 0.30, "cut": 0.40, "diagram": 0.45, "why": 0.45,
             "payoff": 0.40, "end": 0.50}
    segs = []
    for key in leads:  # filename order == edit order; intermediate parts are skipped
        path = os.path.join(SEG, f"{len(segs) + 1:02d}_{key}.mp4")
        if not os.path.exists(path):
            raise SystemExit(f"missing segment {path} — run a full build first")
        segs.append({"key": key, "path": path, "dur": probe_duration(path),
                     "lead": leads[key]})
    if not segs:
        raise SystemExit("no segments in .demo_work/seg — run a full build first")
    return segs


def finish(vo, segs):
    """Re-mix audio, re-burn captions and re-mux over the existing picture."""
    total = sum(s["dur"] for s in segs)
    print(f"  total {total:.2f}s")
    lst = os.path.join(WORK, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for s in segs:
            f.write(f"file '{os.path.relpath(s['path'], WORK).replace(os.sep, '/')}'\n")
    silent = os.path.join(WORK, "video_silent.mp4")
    sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
        "-c", "copy", silent])
    mastered, t0 = build_audio(segs, total, vo)
    capped = os.path.join(WORK, "video_cap.mp4")
    build_captions(segs, t0, total, silent, capped)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    sh(["ffmpeg", "-v", "error", "-y", "-i", capped, "-i", mastered,
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-shortest", OUT])
    print("  wrote", OUT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-vo", action="store_true")
    ap.add_argument("--only", choices=["cards", "qa", "finish"], default=None)
    a = ap.parse_args()
    os.makedirs(WORK, exist_ok=True)
    if a.only == "cards":
        build_cards()
        return
    if a.only == "qa":
        qa()
        return
    print("[0/5] narration")
    vo = synth_vo(force=not a.skip_vo)
    print(f"  speech {sum(vo.values()):.2f}s")
    if a.only == "finish":
        finish(vo, existing_segs())
    else:
        assemble(vo)
    qa()


if __name__ == "__main__":
    main()
