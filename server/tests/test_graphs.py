"""Graph shapes: two experts, the split, and every wiring reference."""

import pytest

from cinema import graphs


def _nodes(g):
    """Every node dict (the graph also carries a string `_output` annotation)."""
    return [n for n in g.values() if isinstance(n, dict)]


def _samplers(g):
    return [n for n in _nodes(g) if n.get("class_type") == "KSamplerAdvanced"]


def _output_kind(g, probe):
    return graphs.choose_video_output(probe)


def test_t2v_has_two_experts_split_at_the_boundary():
    g = graphs.t2v("a storm", width=480, height=480, frames=33, steps=8, split=4, seed=7)
    assert graphs.validate_graph(g) == []
    samplers = _samplers(g)
    assert len(samplers) == 2, "one expert for all steps renders noise"
    high, low = samplers
    assert high["inputs"]["add_noise"] == "enable"
    assert high["inputs"]["return_with_leftover_noise"] == "enable"
    assert high["inputs"]["start_at_step"] == 0 and high["inputs"]["end_at_step"] == 4
    assert low["inputs"]["add_noise"] == "disable"
    assert low["inputs"]["start_at_step"] == 4 and low["inputs"]["end_at_step"] == 10000
    # the two samplers sit on different UNETs
    high_unet = g[high["inputs"]["model"][0]]
    low_unet = g[low["inputs"]["model"][0]]
    assert high_unet["class_type"] == "ModelSamplingSD3"
    assert low_unet["class_type"] == "ModelSamplingSD3"
    assert high_unet["inputs"]["model"][0] != low_unet["inputs"]["model"][0]
    assert high["inputs"]["steps"] == 8


def test_t2v_uses_the_t2v_filenames():
    g = graphs.t2v("x")
    unets = [n["inputs"]["unet_name"] for n in _nodes(g) if n["class_type"] == "UNETLoader"]
    assert "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors" in unets
    assert "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors" in unets


def test_i2v_uses_the_i2v_filenames_and_image_to_video():
    g = graphs.i2v("start.png", "a dancer", width=480, height=832, frames=81, steps=20, split=10)
    assert graphs.validate_graph(g) == []
    unets = [n["inputs"]["unet_name"] for n in _nodes(g) if n["class_type"] == "UNETLoader"]
    assert unets == [
        "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
    ]
    w2v = [n for n in _nodes(g) if n["class_type"] == "WanImageToVideo"]
    assert len(w2v) == 1
    assert w2v[0]["inputs"]["length"] == 81
    assert g[w2v[0]["inputs"]["start_image"][0]]["class_type"] == "LoadImage"
    # both samplers read the WanImageToVideo conditioning/latent triple
    w2v_id = next(k for k, n in g.items() if n["class_type"] == "WanImageToVideo")
    for sampler in _samplers(g):
        assert sampler["inputs"]["positive"] == [w2v_id, 0]
        assert sampler["inputs"]["negative"] == [w2v_id, 1]
    # the HIGH expert starts from the video latent, the LOW one continues the HIGH
    # expert's output -- if it re-reads the empty latent the clip decodes to mush.
    assert g["20"]["inputs"]["latent_image"] == [w2v_id, 2]
    assert g["21"]["inputs"]["latent_image"] == ["20", 0]
    assert g["9"]["class_type"] == "CLIPVisionLoader"


def test_flf2v_uses_first_last_frame_node():
    g = graphs.flf2v("a.png", "b.png", "a bridge at dusk", frames=33, steps=8, split=4)
    assert graphs.validate_graph(g) == []
    flf = [n for n in _nodes(g) if n["class_type"] == "WanFirstLastFrameToVideo"]
    assert len(flf) == 1
    assert flf[0]["inputs"]["end_image"] == ["12", 0]
    assert len(_samplers(g)) == 2


def test_split_outside_range_is_refused():
    with pytest.raises(graphs.GraphError):
        graphs.t2v("x", steps=8, split=8)
    with pytest.raises(graphs.GraphError):
        graphs.t2v("x", steps=8, split=0)


def test_keyframe_is_sdxl_28_steps():
    g = graphs.keyframe("a portrait", width=832, height=1216, seed=3)
    assert graphs.validate_graph(g) == []
    ckpt = g["1"]
    assert ckpt["class_type"] == "CheckpointLoaderSimple"
    assert ckpt["inputs"]["ckpt_name"] == "RealVisXL_V4.0.safetensors"
    ks = [n for n in _nodes(g) if n["class_type"] == "KSampler"]
    assert len(ks) == 1
    assert ks[0]["inputs"]["steps"] == 28
    assert ks[0]["inputs"]["sampler_name"] == "dpmpp_2m"
    assert ks[0]["inputs"]["scheduler"] == "karras"
    assert ks[0]["inputs"]["cfg"] == 5.0
    assert g["7"]["class_type"] == "SaveImage"


def test_video_output_follows_the_probe():
    core = {"SaveVideo": {}, "CreateVideo": {}, "VHS_VideoCombine": {}}
    assert graphs.choose_video_output(core) == "core_video"
    assert graphs.choose_video_output({"VHS_VideoCombine": {}, "SaveAnimatedWEBP": {}}) == "vhs"
    assert graphs.choose_video_output({"SaveAnimatedWEBP": {}}) == "webp"
    assert graphs.choose_video_output({}) == "core_video"  # assume current core
    # a wrapped probe doc (docs/comfy-probe.json) is unwrapped
    assert graphs.choose_video_output({"object_info": {"SaveAnimatedWEBP": {}}}) == "webp"
    # and an explicit hint wins
    assert graphs.choose_video_output({"video_output": "VHS_VideoCombine"}) == "vhs"


def test_output_nodes_match_the_kind():
    for kind, node_class in (
        ("core_video", "SaveVideo"),
        ("vhs", "VHS_VideoCombine"),
        ("webp", "SaveAnimatedWEBP"),
    ):
        nodes, out_id = graphs.video_output_nodes(kind, "22", "cinema/test", fps=16)
        assert nodes[out_id]["class_type"] == node_class
        assert out_id in nodes

    g = graphs.t2v("x", probe={"SaveAnimatedWEBP": {}})
    classes = [n["class_type"] for n in _nodes(g)]
    assert "SaveAnimatedWEBP" in classes and "SaveVideo" not in classes


def test_latent_node_follows_the_probe():
    assert graphs.choose_latent_node({}) == "EmptyHunyuanLatentVideo"
    g = graphs.t2v("x", probe={"EmptyWanLatentVideo": {}})
    assert g["10"]["class_type"] == "EmptyWanLatentVideo"


def test_build_shot_dispatch_and_validation():
    g = graphs.build_shot("keyframe", "a face", quality="draft")
    assert g["4"]["inputs"]["width"] == 768
    with pytest.raises(graphs.GraphError):
        graphs.build_shot("nope", "x")
    with pytest.raises(graphs.GraphError):
        graphs.build_shot("i2v", "x")  # no image


def test_presets_match_the_contract():
    draft = graphs.preset("draft", "t2v")
    final = graphs.preset("final", "t2v")
    assert (draft["width"], draft["height"], draft["frames"], draft["steps"], draft["split"]) == (
        480, 480, 33, 8, 4,
    )
    assert (final["width"], final["height"], final["frames"], final["steps"], final["split"]) == (
        480, 832, 81, 20, 10,
    )


def test_models_env_override(monkeypatch):
    monkeypatch.setenv("CINEMA_MODELS", '{"sdxl": "custom.safetensors"}')
    g = graphs.keyframe("x")
    assert g["1"]["inputs"]["ckpt_name"] == "custom.safetensors"


def test_api_graph_strips_annotations():
    g = graphs.t2v("x")
    assert "_output" in g
    assert "_output" not in graphs.api_graph(g)


# ---------------------------------------------------------------------------
# engines (Fun Camera, lightx2v LoRAs, LTX-2) and the camera pose map
# ---------------------------------------------------------------------------
def test_every_camera_move_maps_to_a_native_pose():
    from cinema import camera

    moves = camera.move_ids()
    assert len(moves) == 20
    for move in moves:
        pose = graphs.camera_pose(move)
        assert pose in graphs.CAMERA_POSES, (move, pose)


def test_wan_camera_is_the_official_fun_camera_graph():
    g = graphs.wan_camera(
        "start.png", "a slow push", width=832, height=480, frames=81, seed=3, move="dolly_in"
    )
    assert graphs.validate_graph(g) == []
    samplers = _samplers(g)
    assert len(samplers) == 2
    high, low = samplers
    assert high["inputs"]["steps"] == 4 and high["inputs"]["end_at_step"] == 2
    assert low["inputs"]["start_at_step"] == 2 and low["inputs"]["end_at_step"] == 4
    assert high["inputs"]["cfg"] == 1.0
    # the 4-step lightx2v LoRA sits between each UNET and its ModelSamplingSD3
    loras = [n for n in _nodes(g) if n["class_type"] == "LoraLoaderModelOnly"]
    assert len(loras) == 2
    assert all("lightx2v_4steps" in n["inputs"]["lora_name"] for n in loras)
    assert "fun_camera" in _nodes(g)[0]["inputs"]["unet_name"]
    # geometry comes from the embedding, so it can never disagree with the video node
    video = [n for n in _nodes(g) if n["class_type"] == "WanCameraImageToVideo"][0]["inputs"]
    embed = [n for n in _nodes(g) if n["class_type"] == "WanCameraEmbedding"][0]
    assert video["width"] == ["12", 1] and video["height"] == ["12", 2] and video["length"] == ["12", 3]
    assert embed["inputs"]["camera_pose"] == "Zoom In"
    assert embed["inputs"]["width"] == 832 and embed["inputs"]["height"] == 480
    assert embed["inputs"]["length"] == 81


def test_frames_are_snapped_to_the_wan_4n_plus_1_grid():
    g = graphs.wan_camera("s.png", "x", frames=48)
    embed = [n for n in _nodes(g) if n["class_type"] == "WanCameraEmbedding"][0]
    assert embed["inputs"]["length"] == 49


def test_i2v_and_t2v_engines_use_the_right_lora_family():
    g = graphs.wan_lora_t2v("a storm", steps=4, split=2)
    loras = [n["inputs"]["lora_name"] for n in _nodes(g) if n["class_type"] == "LoraLoaderModelOnly"]
    assert len(loras) == 2 and all("t2v_lightx2v" in name for name in loras)
    g = graphs.wan_lora_i2v("s.png", "x", steps=4, split=2)
    loras = [n["inputs"]["lora_name"] for n in _nodes(g) if n["class_type"] == "LoraLoaderModelOnly"]
    assert len(loras) == 2 and all("i2v_lightx2v" in name for name in loras)
    assert any(n["class_type"] == "WanImageToVideo" for n in _nodes(g))


def test_default_engine_follows_the_mode():
    assert graphs.default_engine("keyframe") == "sdxl"
    assert graphs.default_engine("t2v") == "wan_t2v"
    assert graphs.default_engine("i2v") == "wan_camera"
    assert graphs.default_engine("reshoot") == "wan_camera"
    assert graphs.default_engine("flf2v") == "wan_flf2v"


def test_build_shot_dispatches_on_engine_and_geometry_overrides():
    g = graphs.build_shot("i2v", "x", image_name="s.png", quality="draft", engine="wan_i2v")
    assert any(n["class_type"] == "WanImageToVideo" for n in _nodes(g))
    g = graphs.build_shot("i2v", "x", image_name="s.png", quality="draft", engine="wan_camera",
                          width=832, height=480, frames=49)
    embed = [n for n in _nodes(g) if n["class_type"] == "WanCameraEmbedding"][0]
    assert (embed["inputs"]["width"], embed["inputs"]["height"], embed["inputs"]["length"]) == (832, 480, 49)
    with pytest.raises(graphs.GraphError):
        graphs.build_shot("i2v", "x", image_name="s.png", engine="nope")
    with pytest.raises(graphs.GraphError):
        graphs.build_shot("keyframe", "x", engine="wan_camera")


def test_ltx_graph_keeps_the_audio_video_latent():
    g = graphs.build_shot("t2v", "a city at dusk", quality="draft", engine="ltx")
    assert graphs.validate_graph(g) == []
    classes = {n["class_type"] for n in _nodes(g)}
    assert {"LTXAVTextEncoderLoader", "LTXVConditioning", "LTXVConcatAVLatent", "LTXVEmptyLatentAudio",
            "SamplerCustomAdvanced", "LTXVSeparateAVLatent", "VAEDecode"} <= classes
    latent = [n for n in _nodes(g) if n["class_type"] == "EmptyLTXVLatentVideo"][0]["inputs"]
    assert latent["width"] % 64 == 0 and latent["height"] % 64 == 0
    assert (latent["length"] - 1) % 8 == 0


def test_ltx_i2v_puts_the_image_into_the_latent():
    g = graphs.build_shot("i2v", "x", image_name="s.png", quality="draft", engine="ltx")
    assert graphs.validate_graph(g) == []
    assert any(n["class_type"] == "LTXVImgToVideoInplace" for n in _nodes(g))


def test_keyframe_model_map_follows_the_surface():
    local = graphs.build_shot("keyframe", "x")
    assert local["1"]["inputs"]["ckpt_name"] == "RealVisXL_V4.0.safetensors"
    cloud = graphs.build_shot("keyframe", "x", mdl=graphs.models(surface="cloud"))
    assert cloud["1"]["inputs"]["ckpt_name"] == "realvisxlV50_v50Bakedvae.safetensors"


def test_every_wan_engine_hands_the_latent_from_high_to_low():
    """The LOW expert must continue the HIGH expert's latent.

    Re-reading the empty/video latent with ``add_noise: disable`` makes the low
    expert denoise pure noise from step ``split``; Comfy Cloud decodes that to a
    flat brown field (measured 2026-10-30).
    """
    built = {
        "t2v": graphs.wan_lora_t2v("a rooftop", steps=4, split=2),
        "i2v": graphs.wan_lora_i2v("start.png", "a rooftop", steps=4, split=2),
        "camera": graphs.wan_camera("start.png", "a rooftop", steps=4, split=2),
        "ltx-free-flf2v": graphs.flf2v("a.png", "b.png", "a rooftop", steps=8, split=4),
    }
    for name, g in built.items():
        assert g["20"]["inputs"]["latent_image"] != ["20", 0]
        assert g["21"]["inputs"]["latent_image"] == ["20", 0], name
        assert g["20"]["inputs"]["return_with_leftover_noise"] == "enable"
        assert g["21"]["inputs"]["add_noise"] == "disable"
        assert graphs.validate_graph(g) == []


def test_image_engines_encode_the_frame_before_the_video_node():
    """``clip_vision*`` inputs want a CLIP_VISION_OUTPUT, never the loader.

    Wiring ``CLIPVisionLoader`` straight in is a return-type mismatch the cloud
    answers with ``node N: clip_vision_output ... return_type_mismatch`` (measured
    2026-10-30); ``WanFirstLastFrameToVideo`` takes two separate outputs.
    """
    built = {
        "i2v": graphs.i2v("a.png", "x"),
        "lora_i2v": graphs.wan_lora_i2v("a.png", "x", steps=4, split=2),
        "flf2v": graphs.flf2v("a.png", "b.png", "x", steps=8, split=4),
    }
    for name, g in built.items():
        assert graphs.validate_graph(g) == [], name
        encoders = [n for n in _nodes(g) if n["class_type"] == "CLIPVisionEncode"]
        assert len(encoders) == (2 if name == "flf2v" else 1), name
        video = [n for n in _nodes(g) if n["class_type"].startswith("Wan") and "video" in n["class_type"].lower()]
        assert video, name
        inputs = video[0]["inputs"]
        keys = [k for k in inputs if k.startswith("clip_vision")]
        assert keys, name
        for key in keys:
            src = inputs[key]
            assert g[src[0]]["class_type"] == "CLIPVisionEncode", (name, key)


def test_model_sampling_shift_matches_the_official_template():
    """Shift is engine-specific and a wrong value renders a flat field, not an error.

    Official templates (fetched 2026-10-30): t2v 4-step 5.0, i2v 4-step 5.0,
    fun-camera 4-step 8.0, plain 20-step 5.0.
    """
    def shifts(g):
        return [n["inputs"]["shift"] for n in _nodes(g) if n["class_type"] == "ModelSamplingSD3"]

    assert shifts(graphs.wan_lora_t2v("x", steps=4, split=2)) == [5.0, 5.0]
    assert shifts(graphs.wan_lora_i2v("a.png", "x", steps=4, split=2)) == [5.0, 5.0]
    assert shifts(graphs.wan_camera("a.png", "x", steps=4, split=2)) == [8.0, 8.0]
    assert shifts(graphs.t2v("x")) == [5.0, 5.0]
    assert shifts(graphs.flf2v("a.png", "b.png", "x")) == [5.0, 5.0]
