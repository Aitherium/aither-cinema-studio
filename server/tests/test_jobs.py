"""Job store, media store and the ffmpeg helpers (no GPU, no Comfy)."""

import json
import shutil
import subprocess

import pytest

from cinema import ffmpeg
from cinema.jobs import JobStore, JsonStore
from cinema.media import MediaStore, kind_for


def test_inline_job_runs_and_persists(tmp_path):
    jobs = JobStore(tmp_path, inline=True)

    def runner(job_id, job):
        return {"answer": 42}

    job = jobs.create("demo", runner, params={"a": 1})
    assert job["status"] == "done"
    assert job["progress"] == 1.0
    assert job["result"] == {"answer": 42}
    assert job["params"] == {"a": 1}
    saved = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert saved[job["job_id"]]["status"] == "done"


def test_failing_job_records_the_message(tmp_path):
    jobs = JobStore(tmp_path, inline=True)

    def boom(job_id, job):
        raise RuntimeError("ComfyUI rejected the graph")

    job = jobs.create("demo", boom)
    assert job["status"] == "error"
    assert "rejected" in job["error"]


def test_progress_is_clamped(tmp_path):
    jobs = JobStore(tmp_path, inline=True)
    job = jobs.create("demo", lambda jid, rec: None)
    jobs.progress(job["job_id"], 3.0)
    assert jobs.get(job["job_id"])["progress"] == 1.0


def test_restart_marks_unfinished_jobs_as_interrupted(tmp_path):
    rec = {
        "job_id": "j_dead",
        "kind": "shot",
        "status": "running",
        "progress": 0.4,
        "created": "2026-01-01T00:00:00+00:00",
    }
    (tmp_path / "jobs.json").write_text(json.dumps({"j_dead": rec}), encoding="utf-8")
    jobs = JobStore(tmp_path, inline=True)
    recovered = jobs.get("j_dead")
    assert recovered["status"] == "error"
    assert "interrupted" in recovered["error"]


def test_json_store_update_and_delete(tmp_path):
    store = JsonStore(tmp_path / "x.json")
    store.put("a", {"k": 1})
    assert store.update("a", k=2)["k"] == 2
    assert store.update("missing", k=2) is None
    assert store.delete("a") is True
    assert store.all() == []


def test_media_store_round_trip(tmp_path):
    media = MediaStore(tmp_path)
    rec = media.add_bytes(b"\x89PNG", ".png", origin="upload", name="ref.png")
    assert rec["media_id"].startswith("m_") and rec["kind"] == "image"
    assert media.path(rec["media_id"]).read_bytes() == b"\x89PNG"
    assert media.get("m_nope") is None
    assert media.url(rec["media_id"]).startswith("/api/media/")
    again = MediaStore(tmp_path)  # re-reads the index
    assert again.get(rec["media_id"])["filename"] == "ref.png"


def test_media_kind_detection():
    assert kind_for(".mp4") == "video"
    assert kind_for(".webp") == "image"
    assert kind_for(".gif") == "video"
    assert kind_for(".bin") == "file"


HAS_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_frame_at_last_frame_and_at_time(tmp_path):
    src = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=blue:s=64x64:d=0.5:r=8", "-frames:v", "4", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(src)],
        check=True,
    )
    last = ffmpeg.frame_at(src, tmp_path / "last.png")
    at = ffmpeg.frame_at(src, tmp_path / "at.png", 0.0)
    assert last.stat().st_size > 0 and at.stat().st_size > 0


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_concat_renormalises_two_sizes(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    for path, size in ((a, "64x64"), (b, "96x96")):
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             "color=c=red:s=%s:d=0.25:r=8" % size, "-frames:v", "2", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", str(path)],
            check=True,
        )
    out = tmp_path / "cut.mp4"
    ffmpeg.concat([a, b], out)
    info = ffmpeg.probe_video(out)
    assert (info["width"], info["height"]) == (64, 64)
    assert info["fps"] == pytest.approx(8.0, rel=0.01)
