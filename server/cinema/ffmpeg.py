"""Thin ffmpeg/ffprobe helpers: frame grab, normalisation and concat.

Every call shells out with ``-loglevel error`` and raises :class:`FFmpegError`
with the captured stderr, so a failure names the file, not just an exit code.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".gif", ".m4v"}

#: Screen time a still keyframe gets when no beat sheet says otherwise.
STILL_SECONDS = 3.0


class FFmpegError(RuntimeError):
    """ffmpeg/ffprobe exited non-zero or is not installed."""


def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FFmpegError("ffmpeg not found on PATH")
    return exe


def ffprobe_bin() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise FFmpegError("ffprobe not found on PATH")
    return exe


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def run(cmd: Sequence[str], what: str) -> str:
    proc = subprocess.run(list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(
            "%s failed (%s): %s" % (what, proc.returncode, (proc.stderr or proc.stdout or "")[-800:])
        )
    return proc.stdout or ""


def is_video(path: Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def probe_video(path: Path) -> dict:
    """{'width','height','fps'} for the first video stream."""
    out = run(
        [
            ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-of", "json", str(path),
        ],
        "ffprobe %s" % path.name,
    )
    try:
        streams = json.loads(out).get("streams") or []
    except ValueError:
        streams = []
    if not streams:
        raise FFmpegError("no video stream in %s" % path.name)
    s = streams[0]
    num, _, den = str(s.get("r_frame_rate", "24/1")).partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 24.0
    return {"width": int(s.get("width") or 0), "height": int(s.get("height") or 0), "fps": fps or 24.0}


def duration(path: Path) -> float:
    out = run(
        [
            ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
        ],
        "ffprobe duration %s" % Path(path).name,
    )
    try:
        return float((out or "0").strip().splitlines()[0])
    except (IndexError, ValueError):
        return 0.0


def frame_at(video: Path, out_png: Path, at_s: Optional[float] = None) -> Path:
    """Write one PNG frame.  ``at_s=None`` -> the last frame.

    ``-sseof -1`` rewinds one second and ``-update 1`` overwrites the same
    file for every decoded frame, so what survives is the final frame.  Clips
    shorter than a second fall back to ``-ss 0`` (a ``-sseof`` past the start
    of the file is ignored by ffmpeg, which would return the FIRST frame).
    """
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_bin(), "-y", "-loglevel", "error"]
    update_only = False
    if at_s is None:
        if duration(video) > 1.0:
            cmd += ["-sseof", "-1"]
        else:
            cmd += ["-ss", "0"]
        update_only = True
    else:
        cmd += ["-ss", "%.3f" % max(0.0, float(at_s))]
    cmd += ["-i", str(video)]
    if update_only:
        cmd += ["-update", "1", str(out_png)]
    else:
        cmd += ["-frames:v", "1", "-update", "1", str(out_png)]
    run(cmd, "frame grab %s" % Path(video).name)
    if not Path(out_png).exists():
        raise FFmpegError("frame grab produced no file for %s" % Path(video).name)
    return Path(out_png)


def _even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


def normalize(src: Path, out: Path, width: int, height: int, fps: float, duration: float = 3.0) -> Path:
    """One clip at exactly width x height / fps / h264, no audio.

    Still images become ``duration`` seconds of video so a keyframe can be cut
    into a sequence next to real shots.
    """
    cmd = [ffmpeg_bin(), "-y", "-loglevel", "error"]
    if not is_video(src):
        cmd += ["-loop", "1", "-t", "%.3f" % duration, "-i", str(src)]
    else:
        cmd += ["-i", str(src)]
    vf = (
        "scale=%d:%d:force_original_aspect_ratio=decrease,"
        "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%.6g"
        % (width, height, width, height, fps)
    )
    cmd += [
        "-vf", vf, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(out),
    ]
    run(cmd, "normalize %s" % Path(src).name)
    return Path(out)


def probe_audio(path: Path) -> bool:
    """True when the file carries at least one audio stream."""
    out = run(
        [
            ffprobe_bin(), "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
        ],
        "ffprobe audio %s" % Path(path).name,
    )
    return bool((out or "").strip())


def _duration_or(path: Path, fallback: float) -> float:
    try:
        value = duration(path)
    except FFmpegError:
        return fallback
    return value if value > 0.05 else fallback


def music_cut(
    paths: Sequence[Path],
    out: Path,
    durations: Optional[Sequence[float]] = None,
    audio: Optional[Path] = None,
    fps: Optional[float] = None,
    fit_audio: bool = False,
) -> float:
    """Cut clips to a beat sheet, with hard cuts and an optional audio bed.

    ``durations[i]`` is the exact screen time shot ``i`` gets.  A clip is
    retimed with ``setpts`` to hit that length (faster or slower), so a shot
    never pads itself with a frozen tail.  Still images are held for their
    duration instead - a keyframe is a still by nature.

    ``audio`` is muxed as AAC.  By default the track is trimmed (or silence
    padded) to the picture length; with ``fit_audio`` the last shot is
    stretched so the picture ends exactly when the track does.

    Returns the output duration in seconds.  h264 + aac, ``+faststart``.
    """
    paths = [Path(p) for p in paths]
    if not paths:
        raise FFmpegError("nothing to cut")
    if durations is not None and len(durations) != len(paths):
        raise FFmpegError(
            "durations has %d entries for %d clips" % (len(durations), len(paths))
        )

    audio = Path(audio) if audio is not None else None
    if audio is not None and not probe_audio(audio):
        raise FFmpegError("%s carries no audio stream" % audio.name)

    # one common size and frame rate, taken from the first real video
    width, height, rate = 480, 480, float(fps or 16.0)
    for src in paths:
        if not is_video(src):
            continue
        try:
            info = probe_video(src)
        except FFmpegError:
            continue
        if info["width"] and info["height"]:
            width, height = _even(info["width"]), _even(info["height"])
            rate = float(fps or info["fps"] or 16.0)
            break

    targets: List[float] = []
    for i, src in enumerate(paths):
        if durations is not None:
            targets.append(max(0.04, float(durations[i])))
        elif is_video(src):
            targets.append(_duration_or(src, 1.0))
        else:
            targets.append(STILL_SECONDS)

    audio_len = _duration_or(audio, 0.0) if audio is not None else 0.0
    if audio is not None and fit_audio:
        plan = sum(targets)
        if audio_len > plan:
            targets[-1] += audio_len - plan
    total = sum(targets)

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [ffmpeg_bin(), "-y", "-loglevel", "error"]
    source_dur: List[float] = []
    for src, target in zip(paths, targets, strict=False):
        if is_video(src):
            cmd += ["-i", str(src)]
            source_dur.append(_duration_or(src, target))
        else:
            cmd += ["-loop", "1", "-t", "%.3f" % target, "-i", str(src)]
            source_dur.append(target)
    audio_index = len(paths)
    if audio is not None:
        cmd += ["-i", str(audio)]

    filters: List[str] = []
    concat_inputs = ""
    for i, (src, target) in enumerate(zip(paths, targets, strict=False)):
        chain = (
            "scale=%d:%d:force_original_aspect_ratio=decrease,"
            "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1"
            % (width, height, width, height)
        )
        if is_video(src):
            src_s = source_dur[i]
            factor = target / src_s if src_s > 0.001 else 1.0
            chain += ",setpts=%.9f*PTS" % factor
        chain += ",fps=%.6g,format=yuv420p" % rate
        filters.append("[%d:v]%s[v%d]" % (i, chain, i))
        concat_inputs += "[v%d]" % i
    filters.append("%sconcat=n=%d:v=1:a=0[vout]" % (concat_inputs, len(paths)))

    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
    if audio is not None:
        cmd += [
            "-map", "%d:a:0" % audio_index,
            "-af", "apad",
            "-c:a", "aac", "-b:a", "192k",
            "-t", "%.3f" % total,
        ]
    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        "-movflags", "+faststart", str(out),
    ]
    run(cmd, "music cut %s" % out.name)
    return total


def concat(paths: Sequence[Path], out: Path, fps: Optional[float] = None) -> Path:
    """Cut a sequence: normalise every clip to the first clip's size, then concat.

    Re-encoding to one common size is deliberate - Wan output at different
    resolutions (draft vs final) will not concat otherwise.
    """
    paths = [Path(p) for p in paths]
    if not paths:
        raise FFmpegError("nothing to concat")
    name = paths[0].name
    if is_video(paths[0]):
        info = probe_video(paths[0])
        width, height = _even(info["width"] or 480), _even(info["height"] or 480)
        fps = float(fps or info["fps"] or 16.0)
    else:
        width, height, fps = 480, 480, float(fps or 16.0)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cinema-seq-") as tmp:
        tmpdir = Path(tmp)
        norm: List[Path] = []
        for i, src in enumerate(paths):
            dst = tmpdir / ("clip_%03d.mp4" % i)
            normalize(src, dst, width, height, fps)
            norm.append(dst)
        if len(norm) == 1:
            shutil.copyfile(norm[0], out)
        else:
            listing = tmpdir / "list.txt"
            listing.write_text(
                "".join("file '%s'\n" % p.as_posix().replace("'", "'\\''") for p in norm),
                encoding="utf-8",
            )
            run(
                [
                    ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(listing), "-c", "copy", str(out),
                ],
                "concat %s" % name,
            )
    return out
