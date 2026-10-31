"""Grading without photocraft: the ffmpeg engine produces a real, changed output."""

import shutil
import subprocess

import pytest
from PIL import Image, ImageStat

from cinema import grade

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture()
def no_photocraft(monkeypatch):
    monkeypatch.setattr(grade, "find_bin", lambda: None)


def _still(path):
    img = Image.new("RGB", (64, 48))
    for x in range(64):
        for y in range(48):
            img.putpixel((x, y), (x * 4, y * 5, 128))
    img.save(path)
    return path


def _mean(path):
    with Image.open(path) as im:
        return ImageStat.Stat(im.convert("RGB")).mean


@needs_ffmpeg
def test_engine_falls_back_to_ffmpeg(no_photocraft):
    assert grade.engine() == "ffmpeg"
    assert grade.status()["available"] is True


@needs_ffmpeg
@pytest.mark.parametrize("look", [k for k in grade.LOOKS if k != "none"])
def test_every_look_changes_a_still(tmp_path, no_photocraft, look):
    src = _still(tmp_path / "in.png")
    dst = tmp_path / ("%s.png" % look)
    grade.grade(src, dst, look, 1.0)
    assert dst.exists() and _mean(dst) != _mean(src)


@needs_ffmpeg
def test_strength_mixes_toward_the_original(tmp_path, no_photocraft):
    src = _still(tmp_path / "in.png")
    full, half = tmp_path / "full.png", tmp_path / "half.png"
    grade.grade(src, full, "monoContrast", 1.0)
    grade.grade(src, half, "monoContrast", 0.5)
    m0, mf, mh = _mean(src), _mean(full), _mean(half)
    # the half grade sits between the original and the full grade, channel by channel
    assert all(min(a, b) - 2 <= c <= max(a, b) + 2 for a, b, c in zip(m0, mf, mh))


@needs_ffmpeg
def test_a_video_grades_in_one_pass(tmp_path, no_photocraft):
    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=64x48:rate=8:duration=1", "-pix_fmt", "yuv420p", str(src)],
                   check=True)
    dst = tmp_path / "out.mp4"
    grade.grade(src, dst, "tealOrange", 0.8)
    assert dst.exists() and dst.stat().st_size > 0
