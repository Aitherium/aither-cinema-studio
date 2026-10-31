"""The GPU lease: hold it around a render where a door arbitrates, never where none exists."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from cinema import gpu_lease


class Door(BaseHTTPRequestHandler):
    """A minimal /gpu/lease door: acquire, heartbeat, release, with a chosen verdict."""

    verdict = "ok"  # ok | refuse | broken
    seen = []

    def log_message(self, *a):  # keep the test output clean
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        Door.seen.append((self.path, self.headers.get("X-Internal-Key"), body))
        if self.path.endswith("/acquire"):
            if Door.verdict == "refuse":
                self.send_response(409)
                self.end_headers()
                self.wfile.write(b'{"detail":{"reason":"outranked","holder":"bonsai"}}')
                return
            payload = {"ok": True, "token": "tok-1", "granted_mb": 22000}
        elif Door.verdict == "broken":
            self.send_response(500)
            self.end_headers()
            return
        else:
            payload = {"ok": True}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())


@pytest.fixture()
def door(monkeypatch, tmp_path):
    Door.seen = []
    Door.verdict = "ok"
    srv = HTTPServer(("127.0.0.1", 0), Door)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("COMFY_LEASE_URL", f"http://127.0.0.1:{srv.server_port}/gpu/lease")
    monkeypatch.setenv("AITHER_INTERNAL_SECRET", "test-key")
    monkeypatch.setenv("COMFY_LEASE", "1")  # conftest opts out by default
    yield srv
    srv.shutdown()


def test_disabled_without_a_key_and_door(monkeypatch, tmp_path):
    monkeypatch.delenv("AITHER_INTERNAL_SECRET", raising=False)
    monkeypatch.setenv("COMFY_LEASE_KEY_FILE", str(tmp_path / "missing.txt"))
    monkeypatch.delenv("COMFY_LEASE", raising=False)
    assert gpu_lease.enabled() is False
    with gpu_lease.hold() as lease:
        assert lease.token == ""  # no door call, no failure


def test_hold_acquires_and_releases(door):
    with gpu_lease.hold(job_ref="j_1") as lease:
        assert lease.token == "tok-1"
    paths = [p for p, _, _ in Door.seen]
    assert paths[0].endswith("/acquire") and paths[-1].endswith("/release")
    assert Door.seen[0][1] == "test-key"
    assert Door.seen[0][2]["job_ref"] == "j_1"
    assert Door.seen[0][2]["class"] == "interactive_media"


def test_refusal_fails_the_render(door):
    Door.verdict = "refuse"
    with pytest.raises(gpu_lease.LeaseRefused, match="outranked"):
        with gpu_lease.hold():
            pass


def test_an_unreachable_door_renders_unleased(monkeypatch):
    monkeypatch.setenv("AITHER_INTERNAL_SECRET", "k")
    monkeypatch.setenv("COMFY_LEASE", "1")
    monkeypatch.setenv("COMFY_LEASE_URL", "http://127.0.0.1:9/gpu/lease")
    with gpu_lease.hold() as lease:
        assert lease.token == "" and "unleased" in lease.note


def test_off_switches_it_off(door, monkeypatch):
    monkeypatch.setenv("COMFY_LEASE", "0")
    with gpu_lease.hold() as lease:
        assert lease.token == ""
    assert Door.seen == []
