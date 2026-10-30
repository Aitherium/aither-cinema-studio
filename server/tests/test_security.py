"""Settings-write auth and key/host binding: the live key-exfiltration bug.

Every network assertion uses ``httpx.MockTransport`` and records the headers
that would have left the process - no network, no real key. ``KEY`` is a
made-up fixture value.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from cinema.app import build_state, create_app
from cinema.comfy import (
    CLOUD_BASE,
    DEFAULT_KEY_HOST,
    ComfyClient,
    load_stored_settings,
    save_stored_settings,
)
from cinema.security import SettingsAuth, is_loopback_host

KEY = "test-only-key-0123456789"
TOKEN = "test-only-token-abcdef"
ATTACKER = "http://203.0.113.7:8188"
REMOTE = ("203.0.113.9", 51234)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "CINEMA_TOKEN",
        "CINEMA_HOST",
        "CINEMA_CORS_ORIGINS",
        "COMFY_SURFACE",
        "COMFY_BASE_URL",
        "COMFY_API_KEY",
        "COMFY_API_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


def _app(data_dir, fake, **kw):
    state = build_state(data_dir, inline=True, comfy=fake)
    return create_app(state, **kw), state


def _recorded(base_url, data_dir):
    """What a real ComfyClient would send to ``base_url`` (headers recorded)."""
    seen = []

    def handler(request):
        seen.append((str(request.url), dict(request.headers)))
        return httpx.Response(200, json={})

    client = ComfyClient(
        base_url=base_url, data_dir=data_dir, transport=httpx.MockTransport(handler)
    )
    try:
        client.system_stats()
    finally:
        client.close()
    return seen


# -- who may write settings -------------------------------------------------
def test_unauthenticated_nonloopback_settings_write_is_refused(data_dir, fake_comfy):
    app, _ = _app(data_dir, fake_comfy)
    with TestClient(app, client=REMOTE) as c:
        denied = c.put("/api/settings", json={"base_url": CLOUD_BASE, "api_key": KEY})
        assert denied.status_code in (401, 403), denied.text
        assert not (data_dir / "settings.json").exists()

        # a body on the test route is the same kind of write
        probed = c.post("/api/settings/test", json={"base_url": ATTACKER})
        assert probed.status_code in (401, 403), probed.text


def test_nonloopback_write_with_the_bearer_token_is_allowed(
    data_dir, fake_comfy, monkeypatch
):
    monkeypatch.setenv("CINEMA_TOKEN", TOKEN)
    app, _ = _app(data_dir, fake_comfy)
    with TestClient(app, client=REMOTE) as c:
        assert c.put("/api/settings", json={"base_url": CLOUD_BASE}).status_code == 401
        wrong = c.put(
            "/api/settings",
            json={"base_url": CLOUD_BASE},
            headers={"authorization": "Bearer not-the-token"},
        )
        assert wrong.status_code == 403

        ok = c.put(
            "/api/settings",
            json={"base_url": CLOUD_BASE, "api_key": KEY},
            headers={"authorization": "Bearer " + TOKEN},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["key_set"] is True
        assert load_stored_settings(data_dir)["api_key"] == KEY


def test_nonloopback_bind_mints_and_prints_a_token_once(capsys):
    auth = SettingsAuth.from_env(bind_host="0.0.0.0")
    assert auth.generated is True
    assert auth.token and len(auth.token) >= 32
    assert auth.token in capsys.readouterr().err

    quiet = SettingsAuth.from_env(bind_host="127.0.0.1")
    assert quiet.token is None and quiet.generated is False
    assert is_loopback_host("::1") and is_loopback_host("127.0.0.1")
    assert not is_loopback_host("0.0.0.0") and not is_loopback_host("203.0.113.9")


def test_app_bound_to_a_network_address_needs_the_minted_token(
    data_dir, fake_comfy, capsys
):
    app, _ = _app(data_dir, fake_comfy, bind_host="0.0.0.0")
    auth = app.state.settings_auth
    assert auth.generated and auth.token in capsys.readouterr().err
    with TestClient(app, client=REMOTE) as c:
        assert c.put("/api/settings", json={}).status_code == 401
        ok = c.put(
            "/api/settings",
            json={"base_url": "http://127.0.0.1:8188"},
            headers={"authorization": "Bearer " + auth.token},
        )
        assert ok.status_code == 200, ok.text


def test_loopback_write_requires_a_same_or_allowed_origin(
    data_dir, fake_comfy, monkeypatch
):
    app, _ = _app(data_dir, fake_comfy)
    with TestClient(app) as c:  # in-process client -> loopback
        local = c.put("/api/settings", json={"base_url": "http://127.0.0.1:8188"})
        assert local.status_code == 200, local.text

        evil = c.put(
            "/api/settings",
            json={"base_url": CLOUD_BASE},
            headers={"origin": "https://evil.example"},
        )
        assert evil.status_code == 403

        opaque = c.put(
            "/api/settings",
            json={"base_url": CLOUD_BASE},
            headers={"origin": "null"},
        )
        assert opaque.status_code == 403

        same = c.put(
            "/api/settings",
            json={"base_url": CLOUD_BASE},
            headers={"origin": "http://127.0.0.1:8765"},
        )
        assert same.status_code == 200, same.text

    # an operator-allowlisted origin (the Pages UI) is the other allowed case
    monkeypatch.setenv("CINEMA_CORS_ORIGINS", "https://aitherium.github.io")
    app2, _ = _app(data_dir, fake_comfy)
    with TestClient(app2) as c:
        allowed = c.put(
            "/api/settings",
            json={"base_url": CLOUD_BASE},
            headers={"origin": "https://aitherium.github.io"},
        )
        assert allowed.status_code == 200, allowed.text


# -- the key never travels to another host ----------------------------------
def test_repointing_base_url_drops_the_key_and_it_never_follows(data_dir, fake_comfy):
    app, _ = _app(data_dir, fake_comfy)
    with TestClient(app) as c:
        saved = c.put(
            "/api/settings", json={"base_url": CLOUD_BASE + "/", "api_key": KEY}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["key_set"] is True
        stored = load_stored_settings(data_dir)
        assert stored["api_key"] == KEY
        assert stored["api_key_host"] == DEFAULT_KEY_HOST
        assert stored["base_url"] == CLOUD_BASE

        # it is attached to the host it was saved for...
        own = _recorded(CLOUD_BASE, data_dir)
        assert own and own[0][1].get("x-api-key") == KEY
        # ...and to no other host, even before anything is repointed
        away = _recorded(ATTACKER, data_dir)
        assert away and "x-api-key" not in away[0][1]

        # repointing without a replacement key clears the stored key
        moved = c.put("/api/settings", json={"base_url": ATTACKER})
        assert moved.status_code == 200, moved.text
        assert moved.json()["key_set"] is False
        stored = load_stored_settings(data_dir)
        assert "api_key" not in stored and "api_key_host" not in stored

        assert all(
            "x-api-key" not in headers for _url, headers in _recorded(ATTACKER, data_dir)
        )
        assert all(
            "x-api-key" not in headers for _url, headers in _recorded(CLOUD_BASE, data_dir)
        )


def test_stored_key_is_only_attached_to_its_own_host(data_dir):
    save_stored_settings(
        data_dir, base_url=CLOUD_BASE, api_key=KEY, api_key_host=DEFAULT_KEY_HOST
    )
    assert _recorded(CLOUD_BASE, data_dir)[0][1].get("x-api-key") == KEY
    lookalike = "https://cloud.comfy.org.evil.example"
    assert "x-api-key" not in _recorded(lookalike, data_dir)[0][1]


def test_a_legacy_key_binds_to_its_saved_base_then_the_cloud_default(data_dir):
    save_stored_settings(data_dir, base_url="https://comfy.example", api_key=KEY)
    assert _recorded("https://comfy.example", data_dir)[0][1].get("x-api-key") == KEY
    assert "x-api-key" not in _recorded(ATTACKER, data_dir)[0][1]

    save_stored_settings(data_dir, base_url=None, api_key=KEY)
    assert "base_url" not in load_stored_settings(data_dir)
    assert _recorded(CLOUD_BASE, data_dir)[0][1].get("x-api-key") == KEY
    assert "x-api-key" not in _recorded(ATTACKER, data_dir)[0][1]


def test_settings_test_with_a_body_is_guarded(data_dir, fake_comfy, monkeypatch):
    app, _ = _app(data_dir, fake_comfy)
    with TestClient(app) as c:
        # no body: the stored backend is probed, nothing can be repointed
        assert c.post("/api/settings/test").status_code == 200

    with TestClient(app, client=REMOTE) as c:
        assert c.post("/api/settings/test").status_code == 200
        blocked = c.post("/api/settings/test", json={"base_url": ATTACKER})
        assert blocked.status_code in (401, 403), blocked.text

    monkeypatch.setenv("CINEMA_TOKEN", TOKEN)
    app2, _ = _app(data_dir, fake_comfy)
    with TestClient(app2, client=REMOTE) as c:
        wrong = c.post(
            "/api/settings/test",
            json={"base_url": ATTACKER},
            headers={"authorization": "Bearer nope"},
        )
        assert wrong.status_code == 403
        probed = c.post(
            "/api/settings/test",
            json={"base_url": "http://127.0.0.1:9"},
            headers={"authorization": "Bearer " + TOKEN},
        )
        assert probed.status_code == 200, probed.text
        assert probed.json()["ok"] is False
