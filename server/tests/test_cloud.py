"""Comfy Cloud adapter: the /api prefix, job states, the 302, and key hygiene.

Every request goes through ``httpx.MockTransport`` - no network, no key file.
``KEY`` is a made-up fixture value, never a real credential.
"""

import json

import httpx
import pytest

from cinema.comfy import (
    ComfyClient,
    ComfyError,
    Settings,
    load_stored_settings,
    save_stored_settings,
)

KEY = "test-only-0123456789abcdef"
CLOUD = "https://cloud.comfy.org"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("COMFY_SURFACE", "COMFY_BASE_URL", "COMFY_API_KEY", "COMFY_API_KEY_FILE", "CINEMA_MODELS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CINEMA_DATA_DIR", "./data")


def _client(handler, **kw):
    return ComfyClient(
        base_url=kw.pop("base_url", CLOUD),
        api_key=kw.pop("api_key", KEY),
        transport=httpx.MockTransport(handler),
        **kw,
    )


# -- surface / key ladder ---------------------------------------------------
def test_auto_surface_follows_the_base_url():
    assert Settings.resolve(base_url=CLOUD, api_key=None).surface == "cloud"
    assert Settings.resolve(base_url="http://127.0.0.1:8188").surface == "local"
    local = ComfyClient(base_url="http://127.0.0.1:8188", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert local.prefix == "" and local.cloud is False
    cloud = ComfyClient(base_url=CLOUD, api_key=KEY, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert cloud.prefix == "/api" and cloud.cloud is True


def test_explicit_surface_beats_the_url(monkeypatch):
    monkeypatch.setenv("COMFY_SURFACE", "cloud")
    s = Settings.resolve()
    assert s.surface == "cloud" and s.base_url == CLOUD  # the default base follows the surface
    monkeypatch.setenv("COMFY_SURFACE", "local")
    assert Settings.resolve().surface == "local"


def test_key_file_ladder(monkeypatch, tmp_path):
    key_file = tmp_path / "comfy_api_key"
    key_file.write_text(KEY + "\n", encoding="utf-8")
    monkeypatch.setenv("COMFY_API_KEY_FILE", str(key_file))
    s = Settings.resolve(base_url=CLOUD)
    assert s.api_key == KEY and s.api_key_source == "file"
    assert s.key_hint == "..." + KEY[-4:]

    monkeypatch.setenv("COMFY_API_KEY", "env-wins")
    assert Settings.resolve(base_url=CLOUD).api_key == "env-wins"


def test_settings_json_ladder(tmp_path):
    save_stored_settings(tmp_path, api_key=KEY, base_url="https://cloud.comfy.org/")
    assert load_stored_settings(tmp_path)["api_key"] == KEY
    s = Settings.resolve(data_dir=tmp_path)
    assert s.api_key_source == "settings" and s.base_url == CLOUD


# -- the key must never leak -------------------------------------------------
def test_repr_public_and_errors_never_carry_the_key():
    def handler(request):
        return httpx.Response(401, text="bad key " + KEY)

    client = _client(handler)
    assert KEY not in repr(client) and KEY not in str(client)
    assert "<set>" in repr(client)
    assert KEY not in client.settings.__repr__()
    assert KEY not in json.dumps(client.settings.public())
    with pytest.raises(ComfyError) as excinfo:
        client.system_stats()
    assert KEY not in str(excinfo.value) and "<redacted>" in str(excinfo.value)
    # the probe reports the status, never the key
    probe = client.test_credentials()
    assert probe["ok"] is False and probe["status"] == 401
    assert KEY not in json.dumps(probe)


# -- paths and states --------------------------------------------------------
def test_cloud_submit_waits_and_reads_jobs():
    seen = []
    polls = {"n": 0}

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/prompt":
            body = json.loads(request.content)
            assert "client_id" not in body, "client_id is a local-queue field"
            assert body["prompt"]["1"]["class_type"] == "UNETLoader"
            return httpx.Response(200, json={"prompt_id": "job-1"})
        if request.url.path == "/api/jobs/job-1":
            polls["n"] += 1
            if polls["n"] < 2:
                return httpx.Response(200, json={"id": "job-1", "status": "in_progress"})
            return httpx.Response(
                200,
                json={
                    "id": "job-1",
                    "status": "completed",
                    "outputs": {"101": {"images": [{"filename": "clip.mp4", "subfolder": "", "type": "output"}]}},
                },
            )
        return httpx.Response(404, json={"error": "nope"})

    client = _client(handler)
    result = client.run({"1": {"class_type": "UNETLoader", "inputs": {}}}, interval=0.0, timeout=10)
    assert result["prompt_id"] == "job-1"
    assert result["outputs"][0]["filename"] == "clip.mp4"
    assert ("POST", "/api/prompt") in seen and ("GET", "/api/jobs/job-1") in seen


def test_failed_job_raises_with_its_detail():
    def handler(request):
        return httpx.Response(
            200, json={"id": "j", "status": "non_retryable_error", "error_message": "out of credits"}
        )

    client = _client(handler)
    with pytest.raises(ComfyError) as excinfo:
        client.wait("j", timeout=10, interval=0.0)
    assert "out of credits" in str(excinfo.value)


def test_view_redirect_is_followed_without_the_key():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/api/view":
            assert request.headers.get("X-API-Key") == KEY
            return httpx.Response(302, headers={"location": "https://storage.example/signed/clip.mp4"})
        assert "X-API-Key" not in request.headers, "the key must never reach the storage host"
        return httpx.Response(200, content=b"MP4BYTES")

    client = _client(handler)
    data = client.download({"filename": "clip.mp4", "subfolder": "", "type": "output"})
    assert data == b"MP4BYTES"
    assert calls[1].url.host == "storage.example"


def test_upload_and_object_info_take_the_prefix():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/api/upload/image":
            return httpx.Response(200, json={"name": "start.png", "subfolder": ""})
        return httpx.Response(200, json={"UNETLoader": {}})

    client = _client(handler)
    assert client.upload_image(b"PNG", "start.png") == "start.png"
    assert "UNETLoader" in client.object_info()
    assert seen == ["/api/upload/image", "/api/object_info"]


def test_reachable_uses_system_stats_on_cloud():
    def handler(request):
        assert request.url.path == "/api/system_stats"
        return httpx.Response(200, json={"system": {"comfyui_version": "v0.43.2"}})

    client = _client(handler)
    assert client.reachable() is True
    assert client.version() == "v0.43.2"


def test_local_paths_are_still_unprefixed():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        return httpx.Response(200, json={})

    client = ComfyClient(base_url="http://127.0.0.1:8188", transport=httpx.MockTransport(handler))
    assert client.reachable() is True
    assert seen == ["/queue"]
