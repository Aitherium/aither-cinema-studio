"""ComfyUI client against a fake server (httpx MockTransport, no sockets)."""

import json

import httpx
import pytest

from cinema.comfy import ComfyClient, ComfyError, ComfyTimeout


class FakeServer:
    def __init__(self, history_after=0, prompt_error=None):
        self.calls = []
        self.history_after = history_after  # number of /history polls before it lands
        self.history_polls = 0
        self.prompt_error = prompt_error
        self.deleted = []
        self.uploaded = None
        self.prompt_body = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path, dict(request.headers)))
        if path == "/queue" and request.method == "POST":
            body = json.loads(request.content.decode())
            self.deleted.extend(body.get("delete") or [])
            return httpx.Response(200, json={})
        if path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if path == "/object_info":
            return httpx.Response(200, json={"SaveImage": {}})
        if path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "0.3.40"}})
        if path == "/prompt":
            self.prompt_body = json.loads(request.content.decode())
            if self.prompt_error:
                return httpx.Response(200, json=self.prompt_error)
            return httpx.Response(200, json={"prompt_id": "pid-1", "number": 1})
        if path.startswith("/history/"):
            self.history_polls += 1
            if self.history_polls <= self.history_after:
                return httpx.Response(200, json={})
            return httpx.Response(
                200,
                json={
                    "pid-1": {
                        "status": {"completed": True, "status_str": "success"},
                        "outputs": {
                            "101": {
                                "images": [{"filename": "still.png", "subfolder": "sub", "type": "output"}],
                                "gifs": [{"filename": "anim.webp", "subfolder": "", "type": "output"}],
                                "videos": [{"filename": "clip.mp4", "subfolder": "renders", "type": "output"}],
                            }
                        },
                    }
                },
            )
        if path == "/upload/image":
            raw = request.content
            self.uploaded = raw
            return httpx.Response(200, json={"name": "up.png", "subfolder": "", "type": "input"})
        if path == "/view":
            return httpx.Response(200, content=b"BYTES")
        return httpx.Response(404, text="nope")


def client(server, **kw):
    return ComfyClient(
        base_url="https://comfy.test", api_key="secret-key", transport=httpx.MockTransport(server), **kw
    )


def test_submit_sends_api_key_and_strips_annotations():
    server = FakeServer()
    c = client(server)
    pid = c.submit({"1": {"class_type": "X", "inputs": {}}, "_output": "1"})
    assert pid == "pid-1"
    method, path, headers = next(call for call in server.calls if call[1] == "/prompt")
    assert method == "POST"
    assert headers.get("x-api-key") == "secret-key"
    assert "_output" not in server.prompt_body["prompt"]


def test_collect_outputs_reads_images_gifs_and_videos():
    entry = {
        "outputs": {
            "9": {
                "images": [{"filename": "a.png", "subfolder": "s", "type": "output"}],
                "gifs": [{"filename": "b.webp"}],
                "videos": [{"filename": "c.mp4", "subfolder": "v"}],
            }
        }
    }
    out = ComfyClient.collect_outputs(entry)
    assert [o["filename"] for o in out] == ["c.mp4", "b.webp", "a.png"]
    assert out[0]["kind"] == "video" and out[0]["subfolder"] == "v"
    assert out[2]["kind"] == "image" and out[2]["subfolder"] == "s"


def test_run_waits_and_returns_outputs():
    server = FakeServer(history_after=2)
    c = client(server)
    result = c.run({"1": {"class_type": "X", "inputs": {}}}, timeout=5, interval=0.001)
    assert result["prompt_id"] == "pid-1"
    assert result["outputs"][0]["filename"] == "clip.mp4"
    assert server.history_polls >= 3


def test_timeout_dequeues_the_prompt():
    server = FakeServer(history_after=10**9)
    c = client(server)
    with pytest.raises(ComfyTimeout):
        c.wait("pid-1", timeout=0.05, interval=0.005)
    assert server.deleted == ["pid-1"], "a client give-up must POST /queue {delete:[pid]}"


def test_prompt_rejection_is_loud():
    server = FakeServer(prompt_error={"error": {"type": "prompt_outputs_failed_validation"}})
    c = client(server)
    with pytest.raises(ComfyError):
        c.submit({"1": {"class_type": "X", "inputs": {}}})
    server2 = FakeServer(prompt_error={"node_errors": {"3": {"errors": ["bad"]}}})
    with pytest.raises(ComfyError):
        client(server2).submit({"1": {"class_type": "X", "inputs": {}}})


def test_upload_and_download_round_trip():
    server = FakeServer()
    c = client(server)
    name = c.upload_image(b"\x89PNG", "ref.png")
    assert name == "up.png"
    assert server.uploaded and b"\x89PNG" in server.uploaded
    out = {"filename": "clip.mp4", "subfolder": "r", "type": "output"}
    assert c.download(out) == b"BYTES"


def test_version_and_reachable():
    server = FakeServer()
    c = client(server)
    assert c.version() == "0.3.40"
    assert c.reachable() is True


def test_http_error_is_a_comfy_error():
    c = ComfyClient(base_url="https://x.test", transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(ComfyError):
        c.object_info()


def _sleepy_comfy(flag):
    """/queue answers 503 until the wake command has written ``flag``."""
    def handler(request: httpx.Request) -> httpx.Response:
        if flag.exists():
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        return httpx.Response(503, text="asleep")
    return handler


def test_wake_cmd_starts_a_sleeping_comfy(tmp_path, monkeypatch):
    import sys

    flag = tmp_path / "awake"
    monkeypatch.delenv("COMFY_API_KEY", raising=False)
    c = ComfyClient(base_url="http://127.0.0.1:8188", transport=httpx.MockTransport(_sleepy_comfy(flag)))
    assert c.reachable() is False
    cmd = '"%s" -c "open(r\'%s\', \'w\').close()"' % (sys.executable, flag)
    seen = []
    assert c.ensure_awake(wake_cmd=cmd, timeout=10, on_status=seen.append) is True
    assert flag.exists() and seen == ["waking ComfyUI"]


def test_wake_cmd_not_run_when_awake_or_unset(tmp_path, monkeypatch):
    flag = tmp_path / "awake"
    flag.touch()
    marker = tmp_path / "ran"
    monkeypatch.delenv("COMFY_WAKE_CMD", raising=False)
    monkeypatch.delenv("COMFY_API_KEY", raising=False)
    c = ComfyClient(base_url="http://127.0.0.1:8188", transport=httpx.MockTransport(_sleepy_comfy(flag)))
    assert c.ensure_awake() is True  # unset: nothing runs
    assert c.ensure_awake(wake_cmd="echo x > \"%s\"" % marker, timeout=1) is True
    assert not marker.exists()  # already awake: the command never runs


def test_wake_gives_up_when_comfy_never_answers(tmp_path, monkeypatch):
    monkeypatch.delenv("COMFY_API_KEY", raising=False)
    c = ComfyClient(base_url="http://127.0.0.1:8188",
                    transport=httpx.MockTransport(_sleepy_comfy(tmp_path / "never")))
    assert c.ensure_awake(wake_cmd="exit 0", timeout=0.5) is False
