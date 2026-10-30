"""Film grade: run the grading engine (photocraft-cli) over a still or a video.

A still is one CLI call.  A video is split to PNG frames, each frame is graded,
and the frames are re-encoded to h264 mp4 at the source frame rate.

The CLI **silently coerces** an unknown ``lut`` to ``none``, so this module
validates the look against the enum itself before spawning anything.  Strength
is applied here (the CLI has no strength parameter): the graded frame is
blended with the original.

``PHOTOCRAFT_BIN`` wins; then ``photocraft-cli`` on PATH; then the private
install under ``~/.aither/photocraft/`` (the only place it exists on the
reference host, where it is not on PATH).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from . import ffmpeg

#: The engine's enum, verbatim.  Anything else is rejected before the call.
LOOKS: Tuple[str, ...] = (
    "none",
    "warm",
    "cool",
    "tealOrange",
    "bleachBypass",
    "fadedFilm",
    "dayForNight",
    "monoContrast",
    "crossProcess",
)

LOOK_LABELS = {
    "none": "None",
    "warm": "Warm",
    "cool": "Cool",
    "tealOrange": "Teal & Orange",
    "bleachBypass": "Bleach Bypass",
    "fadedFilm": "Faded Film",
    "dayForNight": "Day For Night",
    "monoContrast": "Mono Contrast",
    "crossProcess": "Cross Process",
}

CLI_COMMAND = "image.adjustments.colorLookup"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".exr", ".psd", ".pcraft"}


class GradeError(RuntimeError):
    """The grade could not be produced (missing binary, bad look, CLI failure)."""


def _candidates() -> list:
    out = []
    env = os.environ.get("PHOTOCRAFT_BIN")
    if env:
        out.append(Path(env).expanduser())
    found = shutil.which("photocraft-cli")
    if found:
        out.append(Path(found))
    home = Path.home() / ".aither" / "photocraft"
    for name in ("photocraft-cli", "photocraft-cli.exe"):
        out.append(home / name)
    return out


def find_bin() -> Optional[str]:
    """Absolute path to a usable grading CLI, or None."""
    for cand in _candidates():
        try:
            if cand.is_file():
                return str(cand)
        except OSError:  # pragma: no cover - defensive
            continue
    return None


def available() -> bool:
    return find_bin() is not None


def status() -> dict:
    b = find_bin()
    return {"available": b is not None, "bin": b}


def validate_look(look: Optional[str]) -> str:
    value = (look or "none").strip()
    if value not in LOOKS:
        raise GradeError("unknown grade look %r (choose one of: %s)" % (look, ", ".join(LOOKS)))
    return value


def cli_bin() -> str:
    b = find_bin()
    if not b:
        raise GradeError(
            "grading CLI not found: set PHOTOCRAFT_BIN or put photocraft-cli on PATH"
        )
    return b


def _run_cli(src: Path, dst: Path, look: str) -> None:
    params = '{"lut":"%s"}' % look
    cmd = [cli_bin(), "run", str(src), "--cmd", CLI_COMMAND, "--params", params, "--out", str(dst)]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0 or not Path(dst).exists():
        raise GradeError(
            "grade failed on %s (%s): %s"
            % (Path(src).name, proc.returncode, (proc.stderr or proc.stdout or "")[-600:])
        )


def _blend(original: Path, graded: Path, strength: float) -> None:
    """Blend graded over original in place: strength 0 -> original, 1 -> graded."""
    from PIL import Image

    with Image.open(original) as base, Image.open(graded) as top:
        base_rgb = base.convert("RGB")
        top_rgb = top.convert("RGB")
        if base_rgb.size != top_rgb.size:
            top_rgb = top_rgb.resize(base_rgb.size)
        mixed = Image.blend(base_rgb, top_rgb, max(0.0, min(1.0, float(strength))))
        mixed.save(graded, format="PNG")


def grade_image(src: Path, dst: Path, look: str = "none", strength: float = 1.0) -> Path:
    """Grade one still.  Raises GradeError/FFmpegError."""
    look = validate_look(look)
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if look == "none" or float(strength) <= 0.0:
        shutil.copyfile(src, dst)
        return dst
    if float(strength) >= 1.0:
        _run_cli(src, dst, look)
        return dst
    tmp = dst.with_name(dst.stem + ".graded.png")
    try:
        _run_cli(src, tmp, look)
        _blend(src, tmp, strength)
        shutil.move(str(tmp), str(dst))
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return dst


def grade_video(
    src: Path, dst: Path, look: str = "none", strength: float = 1.0, fps: Optional[float] = None
) -> Path:
    """Grade a video: split to PNG frames, grade each, re-encode h264 mp4."""
    look = validate_look(look)
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if look == "none" or float(strength) <= 0.0:
        shutil.copyfile(src, dst)
        return dst
    info = ffmpeg.probe_video(src)
    rate = float(fps or info["fps"] or 16.0)
    with tempfile.TemporaryDirectory(prefix="cinema-grade-") as tmp:
        tmpdir = Path(tmp)
        frames_in = tmpdir / "in"
        frames_out = tmpdir / "out"
        frames_in.mkdir()
        frames_out.mkdir()
        ffmpeg.run(
            [
                ffmpeg.ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(src),
                "-vsync", "0", str(frames_in / "%06d.png"),
            ],
            "split %s" % src.name,
        )
        frames = sorted(frames_in.glob("*.png"))
        if not frames:
            raise GradeError("no frames extracted from %s" % src.name)
        for frame in frames:
            grade_image(frame, frames_out / frame.name, look, strength)
        ffmpeg.run(
            [
                ffmpeg.ffmpeg_bin(), "-y", "-loglevel", "error",
                "-framerate", "%.6g" % rate,
                "-i", str(frames_out / "%06d.png"),
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(dst),
            ],
            "re-encode %s" % dst.name,
        )
    return dst


def grade(src: Path, dst: Path, look: str = "none", strength: float = 1.0, fps: Optional[float] = None) -> Path:
    """Dispatch still vs video on the source extension."""
    src = Path(src)
    if ffmpeg.is_video(src):
        return grade_video(src, dst, look, strength, fps=fps)
    return grade_image(src, dst, look, strength)


def looks_table() -> list:
    return [{"id": k, "label": LOOK_LABELS[k]} for k in LOOKS]
