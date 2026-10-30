"""Camera rig: the vocabulary is data, compilation is deterministic."""

from pathlib import Path

import pytest

from cinema import camera

VOCAB_JSON = Path(camera.__file__).with_name("camera_vocab.json")
PACK_DIR = Path(camera.__file__).parents[2] / "comfy_nodes" / "ComfyUI-CinemaRig"
PACK_JSON = PACK_DIR / "camera_vocab.json"


def test_vocabulary_matches_the_contract():
    assert len(camera.move_ids()) == 20
    assert camera.lens_ids() == ["14mm", "24mm", "35mm", "50mm", "85mm", "135mm", "anamorphic"]
    assert camera.speed_ids() == ["slow", "medium", "fast"]
    assert len(camera.framing_ids()) == 10
    for expected in ("static", "dolly_in", "whip_pan", "rack_focus", "zoom_out"):
        assert expected in camera.move_ids()


def test_compile_is_deterministic_and_shaped():
    rig = {"move": "dolly_in", "lens": "35mm", "speed": "slow", "framing": "medium"}
    a = camera.compile(rig, "a lone astronaut walks a neon street")
    b = camera.compile(rig, "a lone astronaut walks a neon street")
    assert a == b
    assert a.startswith("a lone astronaut walks a neon street.")
    assert "dollies in" in a
    assert "slowly" in a
    assert "35mm" in a
    assert a.rstrip().endswith("waist up.")


def test_speed_changes_the_adverb():
    base = {"move": "pan_left", "lens": "50mm", "framing": "wide"}
    slow = camera.compile({**base, "speed": "slow"}, "")
    fast = camera.compile({**base, "speed": "fast"}, "")
    assert "slowly pans left" in slow
    assert "quickly pans left" in fast
    assert slow != fast


def test_static_ignores_speed():
    a = camera.compile({"move": "static", "speed": "slow", "lens": "50mm", "framing": "wide"}, "x")
    b = camera.compile({"move": "static", "speed": "fast", "lens": "50mm", "framing": "wide"}, "x")
    assert a == b


def test_every_move_compiles():
    for move in camera.move_ids():
        text = camera.compile({"move": move}, "a beach at dawn")
        assert "a beach at dawn." in text
        assert len(text) > 40


def test_unknown_values_are_rejected():
    with pytest.raises(camera.CameraError):
        camera.compile({"move": "teleport"}, "x")
    with pytest.raises(camera.CameraError):
        camera.compile({"lens": "8mm"}, "x")
    with pytest.raises(camera.CameraError):
        camera.validate_rig({"framing": "nowhere"})


def test_defaults_fill_in():
    rig = camera.validate_rig({})
    assert rig == {"move": "static", "lens": "50mm", "speed": "slow", "framing": "medium"}


def test_vocab_payload_is_ui_shaped():
    v = camera.vocab()
    assert {"version", "moves", "lenses", "speeds", "framings"} <= set(v)
    assert all({"id", "label", "clause"} <= set(m) for m in v["moves"])


def test_node_pack_vocabulary_is_byte_identical():
    assert PACK_JSON.is_file(), "the node pack must ship camera_vocab.json"
    assert VOCAB_JSON.read_bytes() == PACK_JSON.read_bytes()


def _load_pack():
    """Import the ComfyUI node pack by path (it needs no torch at import time)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("cinema_rig_pack", PACK_DIR / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_node_pack_has_the_two_nodes_and_clean_display_names():
    pack = _load_pack()
    assert set(pack.NODE_CLASS_MAPPINGS) == {"CinemaCameraRig", "CinemaFilmGrade"}
    assert pack.NODE_DISPLAY_NAME_MAPPINGS == {
        "CinemaCameraRig": "Cinema Camera Rig",
        "CinemaFilmGrade": "Cinema Film Grade",
    }
    for name in pack.NODE_DISPLAY_NAME_MAPPINGS.values():
        assert "photocraft" not in name.lower()


def test_node_pack_compiles_to_the_same_text_as_the_backend():
    pack = _load_pack()
    subject = "a lone astronaut walks a neon street"
    for move in camera.move_ids():
        for speed in camera.speed_ids():
            for lens in camera.lens_ids():
                for framing in camera.framing_ids():
                    rig = {"move": move, "lens": lens, "speed": speed, "framing": framing}
                    assert pack.compile_prompt(subject, move, lens, speed, framing) == camera.compile(
                        rig, subject
                    ), (move, lens, speed, framing)


def test_node_pack_rig_node_is_callable():
    pack = _load_pack()
    node = pack.CinemaCameraRig()
    (text,) = node.compile("a beach", "orbit_left", "85mm", "fast", "close_up")
    assert text.startswith("a beach.")
    assert "quickly orbits left" in text and "85mm" in text
