"""The MCP prompt compiler must match the studio's own camera table.

``server/cinema/camera.py`` compiles the authoritative prompt at submit time;
``plan_shot`` compiles from the same vocabulary so an agent can read the text
before spending a render.  This test pins the two together: same rig, same
subject, same bytes.
"""

from __future__ import annotations

import pytest

from cinema_mcp.prompt import compile_prompt, validate_rig

cinema_camera = pytest.importorskip("cinema.camera")


def _api_vocab() -> dict:
    return cinema_camera.vocab()


@pytest.mark.parametrize(
    "rig",
    [
        None,
        {"move": "dolly_in", "lens": "35mm", "speed": "slow", "framing": "wide"},
        {"move": "whip_pan", "lens": "anamorphic", "speed": "fast", "framing": "dutch"},
        {"move": "static", "lens": "14mm", "speed": "medium", "framing": "birds_eye"},
    ],
)
@pytest.mark.parametrize("subject", ["", "a red kite over a cold sea", "a kite."])
def test_compile_matches_the_backend_byte_for_byte(rig, subject):
    vocab = _api_vocab()
    assert compile_prompt(rig or {}, subject, vocab) == cinema_camera.compile(rig, subject)


def test_validate_rig_fills_the_same_defaults_as_the_backend():
    assert validate_rig(None, _api_vocab()) == cinema_camera.validate_rig(None)
