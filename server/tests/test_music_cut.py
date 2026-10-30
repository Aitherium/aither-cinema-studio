"""POST /api/sequence -- the beat-sheet music cut, against real ffmpeg.

Every clip and the track are synthesised by ffmpeg into a tmp dir, so this
suite proves the picture is really retimed and the audio really muxed —
ffprobe reads the result back.  It skips (never silently passes) when ffmpeg
is not installed.
"""

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from cinema import ffmpeg
from cinema.app import build_state, create_app

pytestmark = pytest.mark.skipif(
    not ffmpeg.available(), reason="ffmpeg/ffprobe not on PATH"
)


def _run(cmd):
    subprocess.run(cmd, capture_output=True, text=True, check=True)


@pytest.fixture()
def work(tmp_path):
    return tmp_path / "media"


def clip(path, seconds, color="red", size="320x240", fps=16):
    _run(
        [
            ffmpeg.ffmpeg_bin(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=%s:s=%s:r=%d:d=%.3f" % (color, size, fps, seconds),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ]
    )
    return path


def wav(path, seconds, freq=440):
    _run(
        [
            ffmpeg.ffmpeg_bin(), "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", "sine=frequency=%d:sample_rate=44100:duration=%.3f" % (freq, seconds),
            "-c:a", "pcm_s16le", str(path),
        ]
    )
    return path


def streams(path):
    out = subprocess.run(
        [
            ffmpeg.ffprobe_bin(), "-v", "error",
            "-show_entries", "stream=codec_type,codec_name:format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    doc = json.loads(out)
    streams = doc.get("streams") or []
    return {
        "duration": float(doc["format"]["duration"]),
        "video": any(s.get("codec_type") == "video" for s in streams),
        "audio": any(s.get("codec_type") == "audio" for s in streams),
        "video_codec": next(
            (s.get("codec_name") for s in streams if s.get("codec_type") == "video"), None
        ),
        "audio_codec": next(
            (s.get("codec_name") for s in streams if s.get("codec_type") == "audio"), None
        ),
    }


def make_shot(state, path, sid, color="red", seconds=2.0):
    src = clip(path, seconds, color=color)
    rec = state.media.add_file(src, origin="render", ext=".mp4")
    state.shots.put(
        sid,
        {
            "shot_id": sid,
            "media_id": rec["media_id"],
            "graded_media_id": None,
            "status": "done",
        },
    )
    return rec["media_id"]


def make_keyframe(state, path, sid, color="green"):
    _run(
        [
            ffmpeg.ffmpeg_bin(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=%s:s=320x240:d=1" % color,
            "-frames:v", "1", str(path),
        ]
    )
    rec = state.media.add_file(path, origin="render", ext=".png")
    state.shots.put(
        sid,
        {
            "shot_id": sid,
            "media_id": rec["media_id"],
            "graded_media_id": None,
            "status": "done",
        },
    )
    return rec["media_id"]


@pytest.fixture()
def client(data_dir, fake_comfy, work):
    work.mkdir(parents=True, exist_ok=True)
    state = build_state(data_dir, inline=True, comfy=fake_comfy)
    return TestClient(create_app(state)), state


def _upload_audio(c, work, seconds=6.0):
    track = wav(work / ("track_%.1f.wav" % seconds), seconds)
    resp = c.post(
        "/api/upload", files={"file": ("track.wav", track.read_bytes(), "audio/wav")}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["media_id"]


def _fetch(c, work, media_id, name="out.mp4"):
    resp = c.get("/api/media/%s" % media_id)
    assert resp.status_code == 200
    out = work / name
    out.write_bytes(resp.content)
    return out


def test_beat_cut_retimes_each_shot_and_muxes_the_track(client, work):
    c, state = client
    ids = []
    for i, (sid, color) in enumerate((("s_a", "red"), ("s_b", "blue"))):
        make_shot(state, work / ("clip_%d.mp4" % i), sid, color=color, seconds=2.0)
        ids.append(sid)
    audio_id = _upload_audio(c, work, seconds=6.0)

    resp = c.post(
        "/api/sequence",
        json={
            "shot_ids": ids,
            "audio_media_id": audio_id,
            "bpm": 120,
            "beats_per_shot": [2, 4],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["audio_media_id"] == audio_id
    assert body["music_video"] is True
    assert body["beats_per_shot"] == [2, 4]

    job = c.get("/api/jobs/%s" % body["job_id"]).json()
    assert job["status"] == "done", job
    assert job["params"]["audio_media_id"] == audio_id
    assert job["result"]["music_video"] is True
    # 2 beats + 4 beats at 120 bpm = 1s + 2s, and the 6s track is trimmed to it
    assert job["result"]["duration_s"] == pytest.approx(3.0, abs=0.01)

    out = _fetch(c, work, job["result"]["media_id"])
    info = streams(out)
    assert info["video"] and info["audio"]
    assert info["video_codec"] == "h264"
    assert info["audio_codec"] == "aac"
    assert info["duration"] == pytest.approx(3.0, abs=0.2)

    # faststart: the moov atom is front-loaded before mdat
    data = out.read_bytes()
    assert data.find(b"moov") != -1 and data.find(b"mdat") != -1
    assert data.find(b"moov") < data.find(b"mdat")


def test_fit_audio_extends_the_last_shot_to_the_track(client, work):
    c, state = client
    ids = []
    for i, sid in enumerate(("s_a", "s_b")):
        make_shot(state, work / ("clip_%d.mp4" % i), sid, seconds=2.0)
        ids.append(sid)
    audio_id = _upload_audio(c, work, seconds=5.0)

    resp = c.post(
        "/api/sequence",
        json={
            "shot_ids": ids,
            "audio_media_id": audio_id,
            "bpm": 120,
            "beats_per_shot": [2, 2],  # plan is 2s; the track is 5s
            "fit": "audio",
        },
    )
    assert resp.status_code == 200, resp.text
    job = c.get("/api/jobs/%s" % resp.json()["job_id"]).json()
    assert job["status"] == "done", job
    assert job["result"]["duration_s"] == pytest.approx(5.0, abs=0.05)

    out = _fetch(c, work, job["result"]["media_id"])
    info = streams(out)
    assert info["audio"] and info["video"]
    assert info["duration"] == pytest.approx(5.0, abs=0.25)


def test_beat_cut_without_audio_is_silent_but_retimed(client, work):
    c, state = client
    ids = []
    for i, sid in enumerate(("s_a", "s_b")):
        make_shot(state, work / ("clip_%d.mp4" % i), sid, seconds=2.0)
        ids.append(sid)

    resp = c.post(
        "/api/sequence",
        json={"shot_ids": ids, "bpm": 60, "beats_per_shot": [1, 1]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["music_video"] is False
    job = c.get("/api/jobs/%s" % resp.json()["job_id"]).json()
    assert job["status"] == "done", job

    out = _fetch(c, work, job["result"]["media_id"])
    info = streams(out)
    assert info["video"] and not info["audio"]
    assert info["duration"] == pytest.approx(2.0, abs=0.2)


def test_beat_cut_holds_a_still_keyframe_for_its_beats(client, work):
    c, state = client
    make_keyframe(state, work / "key.png", "s_key")
    make_shot(state, work / "clip.mp4", "s_vid", seconds=2.0)
    audio_id = _upload_audio(c, work, seconds=4.0)

    resp = c.post(
        "/api/sequence",
        json={
            "shot_ids": ["s_key", "s_vid"],
            "audio_media_id": audio_id,
            "bpm": 120,
            "beats_per_shot": [2, 2],
            "fit": "audio",
        },
    )
    assert resp.status_code == 200, resp.text
    job = c.get("/api/jobs/%s" % resp.json()["job_id"]).json()
    assert job["status"] == "done", job

    out = _fetch(c, work, job["result"]["media_id"])
    info = streams(out)
    assert info["video"] and info["audio"]
    # the still holds for its beat span; the track then stretches the tail
    assert info["duration"] == pytest.approx(4.0, abs=0.25)


def test_plain_cut_still_concatenates(client, work):
    c, state = client
    ids = []
    for i, sid in enumerate(("s_a", "s_b")):
        make_shot(state, work / ("clip_%d.mp4" % i), sid, seconds=2.0)
        ids.append(sid)

    resp = c.post("/api/sequence", json={"shot_ids": ids})
    assert resp.status_code == 200, resp.text
    assert resp.json()["music_video"] is False
    job = c.get("/api/jobs/%s" % resp.json()["job_id"]).json()
    assert job["status"] == "done", job

    out = _fetch(c, work, job["result"]["media_id"])
    info = streams(out)
    assert info["video"] and not info["audio"]
    assert info["duration"] == pytest.approx(4.0, abs=0.25)


def test_audio_without_beats_is_muxed_to_the_picture(client, work):
    c, state = client
    ids = []
    for i, sid in enumerate(("s_a", "s_b")):
        make_shot(state, work / ("clip_%d.mp4" % i), sid, seconds=2.0)
        ids.append(sid)
    audio_id = _upload_audio(c, work, seconds=8.0)

    resp = c.post(
        "/api/sequence", json={"shot_ids": ids, "audio_media_id": audio_id}
    )
    assert resp.status_code == 200, resp.text
    job = c.get("/api/jobs/%s" % resp.json()["job_id"]).json()
    assert job["status"] == "done", job

    out = _fetch(c, work, job["result"]["media_id"])
    info = streams(out)
    assert info["audio"] and info["video"]
    # the 8s track is trimmed to the 4s picture
    assert info["duration"] == pytest.approx(4.0, abs=0.25)


@pytest.mark.parametrize(
    "extra, detail",
    [
        ({"bpm": 120, "beats_per_shot": [4]}, "one entry per shot"),
        ({"bpm": 120, "beats_per_shot": [0, 4]}, "must be positive"),
        ({"beats_per_shot": [4, 4]}, "bpm is required"),
        ({"bpm": 0}, "bpm must be positive"),
        ({"bpm": 120, "beats_per_shot": [4, 4], "fit": "stretch"}, "fit must be"),
        ({"audio_media_id": "m_missing"}, "not found"),
    ],
)
def test_bad_music_requests_are_rejected(client, extra, detail):
    c, state = client
    ids = []
    for sid in ("s_a", "s_b"):
        state.shots.put(sid, {"shot_id": sid, "media_id": "m_x"})
        ids.append(sid)
    resp = c.post("/api/sequence", json={"shot_ids": ids, **extra})
    assert resp.status_code == 400, resp.text
    assert detail in resp.json()["detail"]
