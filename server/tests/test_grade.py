"""Grade: enum validation always, the real CLI when the binary exists."""

import shutil
import subprocess
from pathlib import Path

import pytest

from cinema import grade

HAS_FFMPEG = shutil.which("ffmpeg") is not None
HAS_CLI = grade.available()

needs_cli = pytest.mark.skipif(not HAS_CLI, reason="grading CLI not installed")
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


def test_look_enum_is_validated_here_not_by_the_cli():
    for look in grade.LOOKS:
        assert grade.validate_look(look) == look
    assert grade.validate_look(None) == "none"
    with pytest.raises(grade.GradeError):
        grade.validate_look("sepia")
    with pytest.raises(grade.GradeError):
        grade.validate_look("WARM")


def test_looks_table_is_ui_shaped():
    rows = grade.looks_table()
    assert len(rows) == 9
    assert {"id": "tealOrange", "label": "Teal & Orange"} in rows


def _make_png(path: Path, color: str = "orange") -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=%s:s=64x64" % color,
         "-frames:v", "1", str(path)],
        check=True,
    )
    return path


@needs_cli
@needs_ffmpeg
def test_grade_a_real_still(tmp_path):
    src = _make_png(tmp_path / "in.png")
    dst = tmp_path / "out.png"
    grade.grade_image(src, dst, "warm", 1.0)
    assert dst.exists() and dst.stat().st_size > 0
    assert dst.read_bytes() != src.read_bytes(), "the graded still must differ from the input"


@needs_cli
@needs_ffmpeg
def test_strength_zero_copies(tmp_path):
    src = _make_png(tmp_path / "in.png")
    dst = tmp_path / "same.png"
    grade.grade_image(src, dst, "tealOrange", 0.0)
    assert dst.read_bytes() == src.read_bytes()


@needs_cli
@needs_ffmpeg
def test_strength_blends(tmp_path):
    src = _make_png(tmp_path / "in.png")
    full = tmp_path / "full.png"
    half = tmp_path / "half.png"
    grade.grade_image(src, full, "monoContrast", 1.0)
    grade.grade_image(src, half, "monoContrast", 0.5)
    assert half.read_bytes() != full.read_bytes()
    assert half.read_bytes() != src.read_bytes()


@needs_cli
@needs_ffmpeg
def test_grade_a_short_video_keeps_frames_and_fps(tmp_path):
    src = tmp_path / "in.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=red:s=64x64:d=0.5:r=8", "-frames:v", "4", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(src)],
        check=True,
    )
    dst = tmp_path / "out.mp4"
    grade.grade_video(src, dst, "cool", 1.0)
    assert dst.exists() and dst.stat().st_size > 0
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames,r_frame_rate", "-of", "default=nw=1", str(dst)],
        capture_output=True, text=True,
    ).stdout
    assert "nb_read_frames=4" in out
    assert "r_frame_rate=8/1" in out


@needs_cli
def test_binary_is_the_private_install_when_not_on_path():
    assert grade.find_bin()
    assert Path(grade.find_bin()).name in ("photocraft-cli", "photocraft-cli.exe")
