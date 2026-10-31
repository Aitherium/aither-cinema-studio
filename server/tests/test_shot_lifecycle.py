"""A job's outcome reaches its shot, and a shot can be deleted (2026-10-30)."""

import httpx
from fastapi.testclient import TestClient

from cinema.app import build_state, create_app
from cinema.comfy import ComfyClient
from cinema.jobs import JobStore


class DeadComfy:
    """Every call fails the way an unreachable ComfyUI does."""

    def reachable(self):
        return False

    def run(self, *a, **kw):
        raise RuntimeError("[WinError 10061] actively refused")


def _shot(state, shot_id="s_test", job_id="j_test", status="running"):
    state.shots.put(shot_id, {"shot_id": shot_id, "status": status, "progress": 0.05,
                              "job_id": job_id, "prompt": "a test"})


def test_a_failed_job_marks_its_shot(tmp_path):
    state = build_state(tmp_path, inline=True, comfy=DeadComfy())
    _shot(state)
    state.jobs.create("shot", lambda job_id, rec: (_ for _ in ()).throw(RuntimeError("boom")),
                      {"shot_id": "s_test"})
    shot = state.shots.get("s_test")
    assert shot["status"] == "error"
    assert "boom" in shot["error"]
    assert shot["progress"] == 1.0


def test_recovery_repairs_a_shot_left_running(tmp_path):
    shots_path = tmp_path / "shots.json"
    state = build_state(tmp_path, comfy=DeadComfy())
    _shot(state)
    state.jobs.jobs.put("j_test", {"job_id": "j_test", "status": "error",
                                   "error": "refused", "params": {"shot_id": "s_test"}})
    assert shots_path.exists()
    again = build_state(tmp_path, comfy=DeadComfy())  # a restart
    shot = again.shots.get("s_test")
    assert shot["status"] == "error" and shot["error"] == "refused"


def test_delete_shot_removes_it_and_its_job(tmp_path):
    state = build_state(tmp_path, inline=True, comfy=DeadComfy())
    _shot(state, status="error")
    state.jobs.jobs.put("j_test", {"job_id": "j_test", "status": "error", "params": {}})
    client = TestClient(create_app(state))
    resp = client.delete("/api/shots/s_test")
    assert resp.status_code == 200 and resp.json() == {"deleted": "s_test"}
    assert state.shots.get("s_test") is None
    assert state.jobs.get("j_test") is None
    assert client.delete("/api/shots/s_test").status_code == 404


def test_delete_cancels_a_running_job(tmp_path):
    store = JobStore(tmp_path, inline=False)
    store.jobs.put("j_run", {"job_id": "j_run", "status": "running", "params": {}})
    assert store.cancel("j_run") is True
    assert store.jobs.get("j_run")["status"] == "cancelled"
    assert store.cancel("j_run") is False  # already terminal


def test_comfy_client_is_not_needed_to_delete(tmp_path):
    """The route must not touch ComfyUI: deleting works with the renderer down."""
    state = build_state(tmp_path, inline=True, comfy=ComfyClient(
        base_url="http://127.0.0.1:9", transport=httpx.MockTransport(
            lambda r: httpx.Response(503, text="down"))))
    _shot(state, status="error")
    client = TestClient(create_app(state))
    assert client.delete("/api/shots/s_test").status_code == 200
