"""HTTP API against the real app and a fake ComfyUI (jobs run inline)."""

import io
import json

import pytest
from fastapi.testclient import TestClient

from cinema import grade as grade_module
from cinema import graphs
from cinema.app import build_state, create_app


@pytest.fixture()
def fake(fake_comfy):
    return fake_comfy


@pytest.fixture()
def client(data_dir, fake):
    state = build_state(data_dir, inline=True, comfy=fake)
    return TestClient(create_app(state)), state


def _make_image(client, name="ref.png", data=b"\x89PNG\r\n\x1a\nfake"):
    resp = client.post("/api/upload", files={"file": (name, io.BytesIO(data), "image/png")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health(client):
    c, _ = client
    body = c.get("/api/health").json()
    assert body["ok"] is True
    assert body["comfy"]["reachable"] is True
    assert body["grade"]["available"] in (True, False)
    assert "bin" in body["grade"]


def test_camera_vocab_route(client):
    c, _ = client
    v = c.get("/api/camera/vocab").json()
    assert len(v["moves"]) == 20 and len(v["lenses"]) == 7 and len(v["framings"]) == 10


def test_upload_list_and_fetch(client):
    c, _ = client
    rec = _make_image(c)
    assert rec["kind"] == "image"
    assert rec["url"] == "/api/media/%s" % rec["media_id"]
    listed = c.get("/api/media").json()
    assert [m["media_id"] for m in listed] == [rec["media_id"]]
    fetched = c.get(rec["url"])
    assert fetched.status_code == 200
    assert fetched.content.startswith(b"\x89PNG")
    assert c.get("/api/media/m_missing").status_code == 404


def test_t2v_shot_end_to_end(client, fake):
    c, state = client
    resp = c.post(
        "/api/shots",
        json={
            "mode": "t2v",
            "prompt": "a red kite over a cold sea",
            "camera": {"move": "dolly_in", "lens": "35mm", "speed": "slow", "framing": "wide"},
            "quality": "draft",
            "seed": 11,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["shot_id"].startswith("s_") and body["job_id"].startswith("j_")

    shot = c.get("/api/shots/%s" % body["shot_id"]).json()
    assert shot["status"] == "done"
    assert shot["media_id"]
    assert shot["seed"] == 11
    assert "dollies in" in shot["compiled_prompt"]
    assert "a red kite over a cold sea." in shot["compiled_prompt"]
    classes = [n["class_type"] for n in shot["graph"].values() if isinstance(n, dict)]
    assert classes.count("KSamplerAdvanced") == 2
    assert "_output" in shot["graph"]

    # the builder annotates the output node id; the client strips it on submit
    submitted = fake.graphs[-1]
    assert "_output" in submitted
    assert "_output" not in graphs.api_graph(submitted)

    job = c.get("/api/jobs/%s" % body["job_id"]).json()
    assert job["status"] == "done"
    assert job["result"]["media_id"] == shot["media_id"]
    assert c.get("/api/media/%s" % shot["media_id"]).content == b"FAKEVIDEO"


def test_keyframe_shot_keeps_a_still(client, fake):
    c, _ = client
    fake.outputs = [
        {"kind": "image", "filename": "kf.png", "subfolder": "", "type": "output", "node": "7"}
    ]
    fake.payload = b"\x89PNGKEYFRAME"
    resp = c.post(
        "/api/shots",
        json={"mode": "keyframe", "prompt": "a face in window light", "quality": "final", "seed": 2},
    )
    assert resp.status_code == 200, resp.text
    shot = c.get("/api/shots/%s" % resp.json()["shot_id"]).json()
    assert shot["status"] == "done"
    assert shot["graph"]["7"]["class_type"] == "SaveImage"
    assert shot["graph"]["1"]["inputs"]["ckpt_name"] == "RealVisXL_V4.0.safetensors"
    assert c.get("/api/media/%s" % shot["media_id"]).content.startswith(b"\x89PNG")


def test_i2v_uploads_the_reference(client, fake):
    c, _ = client
    ref = _make_image(c, "start.png", b"\x89PNGSTART")
    resp = c.post(
        "/api/shots",
        json={
            "mode": "i2v",
            "prompt": "the same street, later",
            "start_media_id": ref["media_id"],
            "camera": {"move": "orbit_left"},
            "quality": "draft",
        },
    )
    assert resp.status_code == 200, resp.text
    shot = c.get("/api/shots/%s" % resp.json()["shot_id"]).json()
    assert shot["status"] == "done"
    assert fake.uploads, "the reference image must be uploaded to ComfyUI"
    name, data = fake.uploads[-1]
    assert name.startswith("cinema_") and data == b"\x89PNGSTART"
    load = [n for n in shot["graph"].values() if isinstance(n, dict) and n["class_type"] == "LoadImage"]
    assert load[0]["inputs"]["image"] == name
    # i2v defaults to the Fun Camera engine: our rig drives WanCameraEmbedding.
    assert shot["engine"] == "wan_camera"
    classes = [n["class_type"] for n in shot["graph"].values() if isinstance(n, dict)]
    assert "WanCameraImageToVideo" in classes and "WanCameraEmbedding" in classes
    assert "WanImageToVideo" not in classes


def test_motion_shots_honour_the_geometry_overrides(client):
    """--width/--height/--frames must reach the graph for image modes, not the preset."""
    c, _ = client
    ref = _make_image(c, "start.png", b"\x89PNGSTART")
    resp = c.post(
        "/api/shots",
        json={
            "mode": "i2v",
            "prompt": "the same street, later",
            "start_media_id": ref["media_id"],
            "camera": {"move": "dolly_in"},
            "quality": "draft",
            "engine": "wan_camera",
            "width": 480,
            "height": 832,
            "frames": 49,
            "steps": 4,
        },
    )
    assert resp.status_code == 200, resp.text
    shot = c.get("/api/shots/%s" % resp.json()["shot_id"]).json()
    assert shot["status"] == "done"
    emb = [n for n in shot["graph"].values() if isinstance(n, dict) and n["class_type"] == "WanCameraEmbedding"][0]
    assert (emb["inputs"]["width"], emb["inputs"]["height"], emb["inputs"]["length"]) == (480, 832, 49)
    assert emb["inputs"]["camera_pose"] == "Zoom In"


def test_flf2v_needs_both_frames(client):
    c, _ = client
    ref = _make_image(c)
    resp = c.post(
        "/api/shots",
        json={"mode": "flf2v", "prompt": "x", "start_media_id": ref["media_id"]},
    )
    assert resp.status_code == 400


def test_reshoot_uses_the_source_frame(client, fake):
    c, _ = client
    src = _make_image(c, "shot.png", b"\x89PNGSRC")
    resp = c.post(
        "/api/shots",
        json={
            "mode": "reshoot",
            "prompt": "",
            "source_media_id": src["media_id"],
            "camera": {"move": "crane_down", "framing": "low_angle", "speed": "fast"},
        },
    )
    assert resp.status_code == 200, resp.text
    shot = c.get("/api/shots/%s" % resp.json()["shot_id"]).json()
    assert shot["status"] == "done"
    assert fake.uploads[-1][1] == b"\x89PNGSRC"
    assert "low angle, looking up at the subject." in shot["compiled_prompt"]
    assert "quickly descends on a crane" in shot["compiled_prompt"]


def test_bad_requests_are_400(client):
    c, _ = client
    assert c.post("/api/shots", json={"mode": "teleport", "prompt": "x"}).status_code == 400
    assert c.post("/api/shots", json={"mode": "t2v", "prompt": ""}).status_code == 400
    assert (
        c.post("/api/shots", json={"mode": "t2v", "prompt": "x", "camera": {"move": "warp"}}).status_code
        == 400
    )
    assert c.post("/api/shots", json={"mode": "i2v", "prompt": "x"}).status_code == 400
    assert c.post("/api/shots", json={"mode": "t2v", "prompt": "x", "quality": "imax"}).status_code == 400
    assert (
        c.post(
            "/api/shots", json={"mode": "i2v", "prompt": "x", "start_media_id": "m_nope"}
        ).status_code
        == 400
    )


def test_missing_shot_is_404(client):
    c, _ = client
    assert c.get("/api/shots/s_nope").status_code == 404
    assert c.get("/api/jobs/j_nope").status_code == 404


def test_list_shots_is_an_array(client):
    c, _ = client
    assert c.get("/api/shots").json() == []
    c.post("/api/shots", json={"mode": "t2v", "prompt": "one", "seed": 0})
    assert len(c.get("/api/shots").json()) == 1


def test_grade_creates_a_new_media(client, monkeypatch):
    c, state = client
    shot = c.get("/api/shots/%s" % c.post("/api/shots", json={"mode": "t2v", "prompt": "x"}).json()["shot_id"]).json()
    calls = {}

    def fake_grade(src, dst, look="none", strength=1.0, fps=None):
        calls.update({"src": str(src), "look": look, "strength": strength})
        dst.write_bytes(b"GRADED")
        return dst

    monkeypatch.setattr(grade_module, "grade", fake_grade)
    resp = c.post(
        "/api/shots/%s/grade" % shot["shot_id"], json={"look": "tealOrange", "strength": 0.6}
    )
    assert resp.status_code == 200, resp.text
    updated = c.get("/api/shots/%s" % shot["shot_id"]).json()
    assert updated["graded_media_id"]
    assert updated["graded_media_id"] != shot["media_id"]
    assert calls["look"] == "tealOrange" and calls["strength"] == 0.6
    assert c.get("/api/media/%s" % updated["graded_media_id"]).content == b"GRADED"


def test_grade_validates_the_look_itself(client):
    c, _ = client
    shot = c.get("/api/shots/%s" % c.post("/api/shots", json={"mode": "t2v", "prompt": "x"}).json()["shot_id"]).json()
    resp = c.post("/api/shots/%s/grade" % shot["shot_id"], json={"look": "sepia"})
    assert resp.status_code == 400
    assert "tealOrange" in resp.json()["detail"]
    assert c.post("/api/shots/%s/grade" % shot["shot_id"], json={"look": "warm", "strength": 2}).status_code == 400


def test_sequence_cuts_the_shots(client, monkeypatch):
    c, state = client
    ids = []
    for prompt in ("first", "second"):
        shot = c.get("/api/shots/%s" % c.post("/api/shots", json={"mode": "t2v", "prompt": prompt}).json()["shot_id"]).json()
        ids.append(shot["shot_id"])

    seen = {}

    def fake_concat(paths, out, fps=None):
        seen["paths"] = [str(p) for p in paths]
        out.write_bytes(b"SEQUENCE")
        return out

    monkeypatch.setattr("cinema.ffmpeg.concat", fake_concat)
    resp = c.post("/api/sequence", json={"shot_ids": ids})
    assert resp.status_code == 200, resp.text
    job = c.get("/api/jobs/%s" % resp.json()["job_id"]).json()
    assert job["status"] == "done", job
    assert len(seen["paths"]) == 2
    media = job["result"]["media_id"]
    assert c.get("/api/media/%s" % media).content == b"SEQUENCE"


def test_sequence_rejects_unknown_shots(client):
    c, _ = client
    assert c.post("/api/sequence", json={"shot_ids": ["s_nope"]}).status_code == 400
    assert c.post("/api/sequence", json={"shot_ids": []}).status_code == 400


def test_unknown_api_path_is_json_404(client):
    c, _ = client
    resp = c.get("/api/nope")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "not found"}


def test_shots_persist_across_a_restart(client, data_dir, fake):
    c, _ = client
    created = c.post("/api/shots", json={"mode": "t2v", "prompt": "persist me", "seed": 5}).json()
    state2 = build_state(data_dir, inline=True, comfy=fake)
    shot = state2.shots.get(created["shot_id"])
    assert shot is not None and shot["status"] == "done" and shot["seed"] == 5
    persisted = json.loads((data_dir / "shots.json").read_text(encoding="utf-8"))
    assert created["shot_id"] in persisted


# ---------------------------------------------------------------------------
# settings (surface, base_url, key) - the key never comes back out
# ---------------------------------------------------------------------------
def test_settings_never_echo_the_key(client, monkeypatch, data_dir):
    c, _ = client
    for name in ("COMFY_API_KEY", "COMFY_API_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    body = c.get("/api/settings").json()
    assert body["key_set"] is False and body["key_hint"] == ""

    secret = "unit-test-key-abcdef1234"
    resp = c.put(
        "/api/settings",
        json={"base_url": "https://cloud.comfy.org/", "api_key": secret},
    )
    assert resp.status_code == 200, resp.text
    assert secret not in resp.text
    body = resp.json()
    assert body["key_set"] is True
    assert body["key_hint"] == "...1234"
    assert body["surface"] == "cloud"
    assert body["base_url"] == "https://cloud.comfy.org"

    stored = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    assert stored["api_key"] == secret
    assert secret not in c.get("/api/settings").text


def test_settings_rejects_a_base_without_a_scheme(client):
    c, _ = client
    assert c.put("/api/settings", json={"base_url": "cloud.comfy.org"}).status_code == 400


def test_settings_test_reports_without_the_key(client):
    c, _ = client
    body = c.post("/api/settings/test").json()
    assert body["ok"] is True and body["surface"] == "local"
    assert "error" in body and body["error"] is None


def test_shots_carry_their_engine(client):
    c, _ = client
    ref = _make_image(c)
    shot = c.get("/api/shots/%s" % c.post(
        "/api/shots",
        json={"mode": "i2v", "prompt": "x", "start_media_id": ref["media_id"], "engine": "wan_i2v"},
    ).json()["shot_id"]).json()
    assert shot["engine"] == "wan_i2v"
    assert c.post("/api/shots", json={"mode": "t2v", "prompt": "x", "engine": "nope"}).status_code == 400
