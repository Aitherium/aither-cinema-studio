"""Browser reachability: configurable origins and Private Network Access.

The public demo runs on https://aitherium.github.io and must be able to talk to
a backend on http://127.0.0.1:8765, which Chrome gates behind a Private Network
Access preflight.
"""

import pytest
from fastapi.testclient import TestClient

from cinema.app import DEFAULT_CORS_ORIGINS, build_state, cors_origins_from_env, create_app

PAGES = "https://aitherium.github.io"


@pytest.fixture()
def client(data_dir, fake_comfy):
    return TestClient(create_app(build_state(data_dir, inline=True, comfy=fake_comfy)))


def test_default_origins_keep_dev_and_add_pages():
    origins = cors_origins_from_env({})
    assert origins == DEFAULT_CORS_ORIGINS
    assert "http://127.0.0.1:5173" in origins
    assert PAGES in origins


def test_origins_from_env_override():
    env = {"CINEMA_CORS_ORIGINS": " https://studio.example.com/ , http://127.0.0.1:9000 "}
    assert cors_origins_from_env(env) == ["https://studio.example.com", "http://127.0.0.1:9000"]


def test_pages_origin_allowed(client):
    resp = client.get("/api/health", headers={"Origin": PAGES})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == PAGES


def test_unknown_origin_not_allowed(client):
    resp = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers


def test_private_network_preflight_answered(client):
    resp = client.options(
        "/api/health",
        headers={
            "Origin": PAGES,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == PAGES
    assert resp.headers["access-control-allow-private-network"] == "true"


def test_pna_header_has_no_effect_on_plain_requests(client):
    resp = client.get("/api/health", headers={"Origin": PAGES})
    assert "access-control-allow-private-network" not in resp.headers


def test_env_override_changes_allowed_origin(monkeypatch, data_dir, fake_comfy):
    monkeypatch.setenv("CINEMA_CORS_ORIGINS", "https://studio.example.com")
    c = TestClient(create_app(build_state(data_dir, inline=True, comfy=fake_comfy)))
    ok = c.get("/api/health", headers={"Origin": "https://studio.example.com"})
    assert ok.headers["access-control-allow-origin"] == "https://studio.example.com"
    blocked = c.get("/api/health", headers={"Origin": PAGES})
    assert "access-control-allow-origin" not in blocked.headers
