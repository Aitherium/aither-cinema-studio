"""Tool behaviour against a fake backend: the request bodies and the answers."""

from __future__ import annotations

import functools

import anyio
import pytest

from cinema_mcp import tools
from cinema_mcp.client import CinemaClient, CinemaError


def run(fn, *args, **kwargs):
    return anyio.run(functools.partial(fn, *args, **kwargs))


# -- status and vocabulary --------------------------------------------------
def test_backend_status_reports_the_chain(client):
    status = run(tools.backend_status, client)
    assert status["ok"] is True
    assert status["backend"] == client.url
    assert status["comfy"]["reachable"] is True
    assert status["ready_to_render"] is True
    assert status["shots"] == {"total": 0, "in_flight": 0}


def test_backend_status_says_when_comfy_is_down(client, backend):
    backend.comfy_reachable = False
    status = run(tools.backend_status, client)
    assert status["ready_to_render"] is False
    assert "ComfyUI" in status["note"]


def test_list_camera_moves_serves_the_studio_table(client):
    moves = run(tools.list_camera_moves, client)
    ids = {m["id"] for m in moves["moves"]}
    assert {"static", "dolly_in", "orbit_left", "whip_pan"} <= ids
    assert len(ids) == 20
    assert {"14mm", "anamorphic"} <= {m["id"] for m in moves["lenses"]}
    assert moves["defaults"] == {
        "move": "static",
        "lens": "50mm",
        "speed": "slow",
        "framing": "medium",
    }


# -- planning ---------------------------------------------------------------
def test_plan_shot_compiles_without_rendering(client, backend):
    plan = run(
        tools.plan_shot,
        client,
        "a lone lighthouse keeper walks into the storm",
        {"move": "dolly_in", "lens": "35mm", "framing": "wide"},
    )
    assert plan["ok"] is True
    assert plan["camera_rig"]["move"] == "dolly_in"
    assert plan["camera_rig"]["speed"] == "slow"  # defaulted
    assert plan["compiled_prompt"].startswith("a lone lighthouse keeper")
    assert "slowly dollies in" in plan["compiled_prompt"]
    assert "35mm" in plan["compiled_prompt"]
    assert plan["rig_summary"].startswith("Dolly In")
    assert backend.posts("/api/shots") == []  # nothing was shot


def test_plan_shot_rejects_an_unknown_move_and_names_the_ids(client):
    with pytest.raises(CinemaError) as err:
        run(tools.plan_shot, client, "x", {"move": "drone_swarm"})
    assert "unknown camera_rig.move" in str(err.value)
    assert "dolly_in" in str(err.value)


def test_plan_shot_is_deterministic(client):
    a = run(tools.plan_shot, client, "a kite over a cold sea.", {"lens": "85mm"})
    b = run(tools.plan_shot, client, "a kite over a cold sea.", {"lens": "85mm"})
    assert a["compiled_prompt"] == b["compiled_prompt"]


# -- rendering --------------------------------------------------------------
def test_render_shot_posts_the_engine_and_rig(client, backend):
    out = run(
        tools.render_shot,
        client,
        "t2v",
        {"move": "crane_up", "lens": "24mm", "speed": "fast", "framing": "extreme_wide"},
        "a city floods at dawn",
        None,
        None,
        "people",
        42,
        "draft",
    )
    assert out["shot_id"].startswith("s_")
    assert out["job_id"].startswith("j_")
    (body,) = backend.posts("/api/shots")
    assert body["mode"] == "t2v"
    assert body["prompt"] == "a city floods at dawn"
    assert body["camera"] == {
        "move": "crane_up",
        "lens": "24mm",
        "speed": "fast",
        "framing": "extreme_wide",
    }
    assert body["negative"] == "people"
    assert body["seed"] == 42
    assert body["quality"] == "draft"


def test_render_shot_requires_a_start_frame_for_i2v(client, backend):
    with pytest.raises(CinemaError) as err:
        run(tools.render_shot, client, "i2v", None, "animate this")
    assert "start_media_id" in str(err.value)
    assert backend.posts("/api/shots") == []


def test_render_shot_surfaces_the_backend_detail(client, backend):
    with pytest.raises(CinemaError) as err:
        run(tools.render_shot, client, "i2v", None, "x", "m_missing")
    assert "start_media_id 'm_missing' not found" in str(err.value)


def test_render_shot_rejects_an_unknown_engine_before_posting(client, backend):
    with pytest.raises(CinemaError) as err:
        run(tools.render_shot, client, "kenburns", None, "x")
    assert "unknown engine" in str(err.value)
    assert backend.posts("/api/shots") == []


def test_reshoot_shot_sends_the_new_rig_and_the_frame_time(client, backend):
    first = run(tools.render_shot, client, "t2v", None, "a storm at sea")
    source = run(tools.get_shot, client, first["shot_id"])["media_id"]
    out = run(
        tools.reshoot_shot,
        client,
        source,
        {"move": "orbit_right", "lens": "85mm", "framing": "close_up"},
        1.5,
    )
    posted = backend.posts("/api/shots")[-1]
    assert posted["mode"] == "reshoot"
    assert posted["source_media_id"] == source
    assert posted["at_s"] == 1.5
    assert posted["camera"]["move"] == "orbit_right"
    assert out["camera_rig"]["lens"] == "85mm"


# -- shots, grading, cuts ---------------------------------------------------
def test_list_and_get_shots_carry_absolute_media_urls(client):
    shot = run(tools.render_shot, client, "keyframe", None, "a red kite")
    listing = run(tools.list_shots, client)
    assert listing["count"] == 1
    row = listing["shots"][0]
    assert row["shot_id"] == shot["shot_id"]
    assert row["media_url"] == "%s/api/media/%s" % (client.url, "m_" + shot["shot_id"])

    one = run(tools.get_shot, client, shot["shot_id"])
    assert one["status"] == "done"
    assert one["media_url"].startswith(client.url)


def test_get_shot_reports_an_unknown_shot(client):
    with pytest.raises(CinemaError) as err:
        run(tools.get_shot, client, "s_nope")
    assert "404" in str(err.value)
    assert "shot not found" in str(err.value)


def test_grade_shot_validates_the_look_then_grades(client, backend):
    shot = run(tools.render_shot, client, "t2v", None, "a field at dusk")
    with pytest.raises(CinemaError) as err:
        run(tools.grade_shot, client, shot["shot_id"], "vintage")
    assert "unknown look" in str(err.value)

    out = run(tools.grade_shot, client, shot["shot_id"], "tealOrange", 0.8)
    assert out["ok"] is True
    assert backend.grades == [{"shot_id": shot["shot_id"], "look": "tealOrange", "strength": 0.8}]


def test_grade_shot_rejects_strength_out_of_range(client):
    with pytest.raises(CinemaError) as err:
        run(tools.grade_shot, client, "s_001", "warm", 1.4)
    assert "between 0 and 1" in str(err.value)


def test_cut_sequence_sends_screen_order(client, backend):
    a = run(tools.render_shot, client, "t2v", None, "shot a")
    b = run(tools.render_shot, client, "t2v", None, "shot b")
    out = run(tools.cut_sequence, client, [b["shot_id"], a["shot_id"]])
    assert out["job_id"].startswith("j_")
    assert backend.sequences == [[b["shot_id"], a["shot_id"]]]


def test_cut_sequence_refuses_an_empty_cut(client):
    with pytest.raises(CinemaError) as err:
        run(tools.cut_sequence, client, [])
    assert "non-empty" in str(err.value)


def test_cut_music_video_builds_an_honest_beat_sheet(client, backend):
    ids = []
    for i in range(4):
        ids.append(run(tools.render_shot, client, "t2v", None, "verse %d" % i)["shot_id"])
    out = run(tools.cut_music_video, client, ids, 120.0, 4, 7.5)
    assert out["per_shot_s"] == 2.0
    assert out["plan_duration_s"] == 8.0
    assert [row["start_s"] for row in out["cut_sheet"]] == [0.0, 2.0, 4.0, 6.0]
    assert out["cut_sheet"][-1]["end_s"] == 8.0
    assert out["track_fit"] == "overruns"
    assert "lower beats_per_shot" in out["advice"]
    assert backend.sequences[-1] == ids
    # the beat grid is sent, or the backend cannot retime the cut
    assert backend.sequence_bodies[-1]["beats_per_shot"] == [4, 4, 4, 4]
    assert backend.sequence_bodies[-1]["bpm"] == 120.0
    assert "audio_media_id" not in backend.sequence_bodies[-1]
    assert out["music_video"] is False


def test_cut_music_video_sends_the_audio_and_per_shot_grid(client, backend):
    ids = [run(tools.render_shot, client, "t2v", None, "x")["shot_id"] for _ in range(2)]
    out = run(
        tools.cut_music_video,
        client,
        ids,
        90.0,
        [4, 8],
        None,
        "m_track",
        "audio",
    )
    assert out["music_video"] is True
    assert out["audio_media_id"] == "m_track"
    assert out["beats_per_shot"] == [4, 8]
    assert out["per_shot_s"] is None  # the grid is not uniform
    body = backend.sequence_bodies[-1]
    assert body == {
        "shot_ids": ids,
        "audio_media_id": "m_track",
        "bpm": 90.0,
        "beats_per_shot": [4, 8],
        "fit": "audio",
    }
    assert "muxes the audio" in out["note"]


def test_cut_music_video_repeats_one_beat_count(client, backend):
    ids = [run(tools.render_shot, client, "t2v", None, "x")["shot_id"] for _ in range(3)]
    out = run(tools.cut_music_video, client, ids, 120.0, 2)
    assert out["beats_per_shot"] == [2, 2, 2]
    assert backend.sequence_bodies[-1]["beats_per_shot"] == [2, 2, 2]


def test_cut_music_video_rejects_a_mismatched_grid(client):
    ids = [run(tools.render_shot, client, "t2v", None, "x")["shot_id"] for _ in range(2)]
    with pytest.raises(CinemaError) as err:
        run(tools.cut_music_video, client, ids, 120.0, [4, 4, 4])
    assert "one entry per shot" in str(err.value)


def test_cut_music_video_fits_the_track(client):
    ids = [run(tools.render_shot, client, "t2v", None, "x")["shot_id"] for _ in range(2)]
    out = run(tools.cut_music_video, client, ids, 90.0, 2, 30.0)
    assert out["per_shot_s"] == 1.333
    assert out["track_fit"] == "fits"
    assert out["tail_s"] > 0


def test_cut_music_video_rejects_a_bad_tempo(client):
    with pytest.raises(CinemaError) as err:
        run(tools.cut_music_video, client, ["s_001"], 0, 4)
    assert "bpm must be positive" in str(err.value)


# -- a backend that is not there -------------------------------------------
def test_an_unreachable_backend_names_the_start_command():
    dead = CinemaClient(url="http://127.0.0.1:1", timeout_s=2.0)
    with pytest.raises(CinemaError) as err:
        run(tools.backend_status, dead)
    message = str(err.value)
    assert "cannot reach the Cinema backend" in message
    assert "127.0.0.1:1" in message
    assert "CINEMA_URL" in message
