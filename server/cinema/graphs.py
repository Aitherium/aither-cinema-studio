"""Graph builders: plain ComfyUI API-format dicts (``{node_id: {class_type, inputs}}``).

Three families:

* ``keyframe`` - SDXL still (RealVis XL, 28 steps, dpmpp_2m/karras, cfg 5).
* ``t2v`` / ``i2v`` / ``flf2v`` - Wan 2.2 14B, **two experts**.  A high-noise
  UNET takes the first ``split`` steps with ``add_noise`` and hands its
  leftover noise to the low-noise UNET for the rest.  Running one expert for
  every step produces noise, so the split is not optional.
* video output - chosen from what ``/object_info`` reports:
  ``SaveVideo``+``CreateVideo`` -> ``VHS_VideoCombine`` -> ``SaveAnimatedWEBP``.

Everything is a pure function of its arguments, so the graph can be rebuilt in
a worker thread (after uploading a reference image) and still match what the
API returned.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

#: Filenames for a self-hosted ComfyUI.
DEFAULT_MODELS: Dict[str, str] = {
    "sdxl": "RealVisXL_V4.0.safetensors",
    "wan_i2v_high": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
    "wan_i2v_low": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
    "wan_t2v_high": "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
    "wan_t2v_low": "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
    "wan_camera_high": "wan2.2_fun_camera_high_noise_14B_fp8_scaled.safetensors",
    "wan_camera_low": "wan2.2_fun_camera_low_noise_14B_fp8_scaled.safetensors",
    "lora_i2v_high": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
    "lora_i2v_low": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
    "lora_t2v_high": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
    "lora_t2v_low": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
    "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "vae": "wan_2.1_vae.safetensors",
    "clip_vision": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
    "ltx_ckpt": "ltx-2-19b-distilled-fp8.safetensors",
    "ltx_text_encoder": "gemma_3_12B_it_fp8_scaled.safetensors",
}

#: Filenames as they are stored on Comfy Cloud (measured 2026-10-30 in
#: ``docs/cloud-probe.json``).  Everything else is shared with ``DEFAULT_MODELS``.
CLOUD_MODELS: Dict[str, str] = {
    "sdxl": "realvisxlV50_v50Bakedvae.safetensors",
}

#: quality preset -> explicit geometry.  Builders still take explicit values;
#: this is only the default the API/CLI resolve.
PRESETS: Dict[str, Dict[str, Any]] = {
    "draft": {
        "width": 480,
        "height": 480,
        "frames": 33,
        "steps": 8,
        "split": 4,
        "fps": 16,
        "keyframe_width": 768,
        "keyframe_height": 768,
    },
    "final": {
        "width": 480,
        "height": 832,
        "frames": 81,
        "steps": 20,
        "split": 10,
        "fps": 16,
        "keyframe_width": 832,
        "keyframe_height": 1216,
    },
}

MODES = ("keyframe", "t2v", "i2v", "flf2v", "reshoot")

#: Engines a shot may ask for.  ``sdxl`` is the keyframe still, the rest are motion.
ENGINES = ("sdxl", "wan_camera", "wan_t2v", "wan_i2v", "wan_flf2v", "ltx")
MOTION_ENGINES = ("wan_camera", "wan_t2v", "wan_i2v", "wan_flf2v", "ltx")

#: The 9 native poses of ``WanCameraEmbedding`` (from live /object_info) and the
#: nearest pose for every move in ``camera_vocab.json``.
CAMERA_POSES = (
    "Static",
    "Pan Up",
    "Pan Down",
    "Pan Left",
    "Pan Right",
    "Zoom In",
    "Zoom Out",
    "Anti Clockwise (ACW)",
    "ClockWise (CW)",
)
MOVE_POSE: Dict[str, str] = {
    "static": "Static",
    "dolly_in": "Zoom In",
    "dolly_out": "Zoom Out",
    "push_in_fast": "Zoom In",
    "zoom_in": "Zoom In",
    "zoom_out": "Zoom Out",
    "truck_left": "Pan Left",
    "truck_right": "Pan Right",
    "pan_left": "Pan Left",
    "pan_right": "Pan Right",
    "whip_pan": "Pan Right",
    "tilt_up": "Pan Up",
    "tilt_down": "Pan Down",
    "crane_up": "Pan Up",
    "crane_down": "Pan Down",
    "orbit_left": "Anti Clockwise (ACW)",
    "arc": "Anti Clockwise (ACW)",
    "orbit_right": "ClockWise (CW)",
    "handheld": "Static",
    "rack_focus": "Static",
}
#: rig speed -> ``WanCameraEmbedding.speed``.  The node's own default is 1.0, and at
#: 1.0 a 49-frame "Zoom In" travels about four times in, so the rig's "slow" starts
#: well under the node default (measured on Comfy Cloud, 2026-10-30).
SPEED_VALUES: Dict[str, float] = {"slow": 0.35, "medium": 0.7, "fast": 1.2}

VIDEO_KINDS = ("core_video", "vhs", "webp")
VIDEO_KIND_BY_NODE = {
    "SaveVideo": "core_video",
    "CreateVideo": "core_video",
    "VHS_VideoCombine": "vhs",
    "SaveAnimatedWEBP": "webp",
}


class GraphError(ValueError):
    """Raised for an impossible graph request (bad split, unknown mode...)."""


def models(
    override: Optional[Mapping[str, str]] = None, surface: str = "local"
) -> Dict[str, str]:
    """Surface defaults <- ``CINEMA_MODELS`` JSON env <- explicit override."""
    out = dict(DEFAULT_MODELS)
    if surface == "cloud":
        out.update(CLOUD_MODELS)
    raw = os.environ.get("CINEMA_MODELS")
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            out.update({str(k): str(v) for k, v in parsed.items()})
    if override:
        out.update({str(k): str(v) for k, v in override.items()})
    return out


def default_engine(mode: str) -> str:
    """The engine a shot uses when it does not name one."""
    if mode == "keyframe":
        return "sdxl"
    if mode == "t2v":
        return "wan_t2v"
    if mode == "flf2v":
        return "wan_flf2v"
    return "wan_camera"


def camera_pose(move: Optional[str]) -> str:
    """Map a rig move onto the nearest ``WanCameraEmbedding`` pose."""
    return MOVE_POSE.get(str(move or "static"), "Static")


def preset(quality: str, mode: str = "t2v") -> Dict[str, Any]:
    """Resolve a quality name into explicit width/height/frames/steps/..."""
    key = quality if quality in PRESETS else "draft"
    p = dict(PRESETS[key])
    if mode == "keyframe":
        p["width"] = p["keyframe_width"]
        p["height"] = p["keyframe_height"]
    return p


# ---------------------------------------------------------------------------
# probe / output-node discovery
# ---------------------------------------------------------------------------
def normalize_probe(probe: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Accept /object_info directly, or a doc wrapping it under a known key."""
    if not isinstance(probe, Mapping):
        return {}
    for key in ("object_info", "nodes", "classes", "class_types"):
        inner = probe.get(key)
        if isinstance(inner, Mapping):
            return dict(inner)
    return dict(probe)


def choose_video_output(probe: Optional[Mapping[str, Any]]) -> str:
    """Return 'core_video' | 'vhs' | 'webp' for the given probe."""
    nodes = normalize_probe(probe)
    hint = probe.get("video_output") if isinstance(probe, Mapping) else None
    if isinstance(hint, str):
        if hint in VIDEO_KIND_BY_NODE:
            return VIDEO_KIND_BY_NODE[hint]
        if hint in VIDEO_KINDS:
            return hint
    if "SaveVideo" in nodes and "CreateVideo" in nodes:
        return "core_video"
    if "VHS_VideoCombine" in nodes:
        return "vhs"
    if "SaveAnimatedWEBP" in nodes:
        return "webp"
    # Nothing reported (no probe yet): assume a current core ComfyUI.
    return "core_video"


def choose_latent_node(probe: Optional[Mapping[str, Any]]) -> str:
    nodes = normalize_probe(probe)
    for name in ("EmptyHunyuanLatentVideo", "EmptyWanLatentVideo", "EmptyLatentVideo"):
        if name in nodes:
            return name
    return "EmptyHunyuanLatentVideo"


def video_output_nodes(
    kind: str, source_id: str, prefix: str, fps: int = 16
) -> Tuple[Dict[str, Any], str]:
    """Nodes that turn a decoded IMAGE batch into a file.  Returns (nodes, output_id)."""
    if kind == "vhs":
        return (
            {
                "100": {
                    "class_type": "VHS_VideoCombine",
                    "inputs": {
                        "images": [source_id, 0],
                        "frame_rate": fps,
                        "loop_count": 0,
                        "filename_prefix": prefix,
                        "format": "video/h264-mp4",
                        "pix_fmt": "yuv420p",
                        "crf": 19,
                        "save_metadata": True,
                        "trim_to_audio": False,
                        "pingpong": False,
                        "save_output": True,
                    },
                }
            },
            "100",
        )
    if kind == "webp":
        return (
            {
                "100": {
                    "class_type": "SaveAnimatedWEBP",
                    "inputs": {
                        "images": [source_id, 0],
                        "filename_prefix": prefix,
                        "fps": fps,
                        "lossless": False,
                        "quality": 90,
                        "method": "default",
                    },
                }
            },
            "100",
        )
    return (
        {
            "100": {
                "class_type": "CreateVideo",
                "inputs": {"images": [source_id, 0], "fps": fps},
            },
            "101": {
                "class_type": "SaveVideo",
                "inputs": {
                    "video": ["100", 0],
                    "filename_prefix": prefix,
                    "format": "auto",
                    "codec": "auto",
                },
            },
        },
        "101",
    )


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------
def keyframe(
    prompt: str,
    negative: str = "",
    width: int = 768,
    height: int = 768,
    steps: int = 28,
    seed: int = 0,
    cfg: float = 5.0,
    mdl: Optional[Mapping[str, str]] = None,
    prefix: str = "cinema/keyframe",
) -> Dict[str, Any]:
    m = models(mdl)
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": m["sdxl"]}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": int(width), "height": int(height), "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": int(seed),
                "steps": int(steps),
                "cfg": float(cfg),
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
            },
        },
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": prefix},
        },
    }


def _wan_common(
    m: Mapping[str, str],
    high_key: str,
    low_key: str,
    prompt: str,
    negative: str,
    shift: float = 5.0,
) -> Dict[str, Any]:
    """The shared head of every Wan graph: two experts, text encoders, vae.

    ``shift`` is 5.0 for the plain 20-step graphs (official t2v / flf2v plain
    paths); the Fun Camera and FLF2V 4-step LoRA paths use 8.0.
    """
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": m[high_key], "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": float(shift)}},
        "3": {"class_type": "UNETLoader", "inputs": {"unet_name": m[low_key], "weight_dtype": "default"}},
        "4": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["3", 0], "shift": float(shift)}},
        "5": {"class_type": "CLIPLoader", "inputs": {"clip_name": m["clip"], "type": "wan"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["5", 0]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["5", 0]}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": m["vae"]}},
    }


def _wan_samplers(
    graph: Dict[str, Any],
    positive: List[Any],
    negative: List[Any],
    latent: List[Any],
    steps: int,
    split: int,
    seed: int,
    cfg: float = 3.5,
    high_model: Optional[List[Any]] = None,
    low_model: Optional[List[Any]] = None,
    end_at: int = 10000,
) -> str:
    """Attach the high/low KSamplerAdvanced pair.  Returns the LOW sampler id."""
    if steps < 2:
        raise GraphError("steps must be >= 2 for a two-expert split")
    if not 0 < split < steps:
        raise GraphError("split must be between 1 and steps-1 (got %r of %r)" % (split, steps))
    graph["20"] = {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model": high_model or ["2", 0],
            "positive": positive,
            "negative": negative,
            "latent_image": latent,
            "add_noise": "enable",
            "noise_seed": int(seed),
            "steps": int(steps),
            "cfg": float(cfg),
            "sampler_name": "euler",
            "scheduler": "simple",
            "start_at_step": 0,
            "end_at_step": int(split),
            "return_with_leftover_noise": "enable",
        },
    }
    graph["21"] = {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model": low_model or ["4", 0],
            "positive": positive,
            "negative": negative,
            # the LOW expert continues the HIGH expert's latent -- feeding it the
            # original noise again re-samples an empty latent at step `split` and
            # decodes to a flat brown field (measured on Comfy Cloud 2026-10-30).
            "latent_image": ["20", 0],
            "add_noise": "disable",
            "noise_seed": int(seed),
            "steps": int(steps),
            "cfg": float(cfg),
            "sampler_name": "euler",
            "scheduler": "simple",
            "start_at_step": int(split),
            "end_at_step": int(end_at),
            "return_with_leftover_noise": "disable",
        },
    }
    return "21"


def _finish_video(
    graph: Dict[str, Any], decoded_id: str, probe: Optional[Mapping[str, Any]], prefix: str, fps: int
) -> Dict[str, Any]:
    kind = choose_video_output(probe)
    nodes, out_id = video_output_nodes(kind, decoded_id, prefix, fps=fps)
    graph.update(nodes)
    graph["_output"] = out_id
    return graph


def t2v(
    prompt: str,
    negative: str = "",
    width: int = 480,
    height: int = 480,
    frames: int = 33,
    steps: int = 20,
    split: int = 10,
    seed: int = 0,
    cfg: float = 3.5,
    mdl: Optional[Mapping[str, str]] = None,
    probe: Optional[Mapping[str, Any]] = None,
    prefix: str = "cinema/t2v",
    fps: int = 16,
) -> Dict[str, Any]:
    m = models(mdl)
    g = _wan_common(m, "wan_t2v_high", "wan_t2v_low", prompt, negative)
    latent_class = choose_latent_node(probe)
    g["10"] = {
        "class_type": latent_class,
        "inputs": {
            "width": int(width),
            "height": int(height),
            "length": int(frames),
            "batch_size": 1,
        },
    }
    low = _wan_samplers(g, ["6", 0], ["7", 0], ["10", 0], steps, split, seed, cfg)
    g["22"] = {"class_type": "VAEDecode", "inputs": {"samples": [low, 0], "vae": ["8", 0]}}
    return _finish_video(g, "22", probe, prefix, fps)


def i2v(
    image_name: str,
    prompt: str,
    negative: str = "",
    width: int = 480,
    height: int = 480,
    frames: int = 33,
    steps: int = 20,
    split: int = 10,
    seed: int = 0,
    cfg: float = 3.5,
    mdl: Optional[Mapping[str, str]] = None,
    probe: Optional[Mapping[str, Any]] = None,
    prefix: str = "cinema/i2v",
    fps: int = 16,
) -> Dict[str, Any]:
    m = models(mdl)
    g = _wan_common(m, "wan_i2v_high", "wan_i2v_low", prompt, negative)
    g["9"] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": m["clip_vision"]}}
    g["10"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    # the video node takes a CLIP_VISION_OUTPUT (an encode of the start frame),
    # not the loader's CLIP_VISION -- wiring the loader in is a validation error.
    g["12"] = {
        "class_type": "CLIPVisionEncode",
        "inputs": {"clip_vision": ["9", 0], "image": ["10", 0], "crop": "none"},
    }
    g["11"] = {
        "class_type": "WanImageToVideo",
        "inputs": {
            "positive": ["6", 0],
            "negative": ["7", 0],
            "vae": ["8", 0],
            "clip_vision_output": ["12", 0],
            "start_image": ["10", 0],
            "width": int(width),
            "height": int(height),
            "length": int(frames),
            "batch_size": 1,
        },
    }
    low = _wan_samplers(g, ["11", 0], ["11", 1], ["11", 2], steps, split, seed, cfg)
    g["22"] = {"class_type": "VAEDecode", "inputs": {"samples": [low, 0], "vae": ["8", 0]}}
    return _finish_video(g, "22", probe, prefix, fps)


def flf2v(
    start_image: str,
    end_image: str,
    prompt: str,
    negative: str = "",
    width: int = 480,
    height: int = 480,
    frames: int = 33,
    steps: int = 20,
    split: int = 10,
    seed: int = 0,
    cfg: float = 3.5,
    mdl: Optional[Mapping[str, str]] = None,
    probe: Optional[Mapping[str, Any]] = None,
    prefix: str = "cinema/flf2v",
    fps: int = 16,
) -> Dict[str, Any]:
    m = models(mdl)
    g = _wan_common(m, "wan_i2v_high", "wan_i2v_low", prompt, negative)
    g["9"] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": m["clip_vision"]}}
    g["10"] = {"class_type": "LoadImage", "inputs": {"image": start_image}}
    g["12"] = {"class_type": "LoadImage", "inputs": {"image": end_image}}
    # two separate CLIP_VISION_OUTPUTs: start and end frame are encoded apart
    g["13"] = {
        "class_type": "CLIPVisionEncode",
        "inputs": {"clip_vision": ["9", 0], "image": ["10", 0], "crop": "none"},
    }
    g["14"] = {
        "class_type": "CLIPVisionEncode",
        "inputs": {"clip_vision": ["9", 0], "image": ["12", 0], "crop": "none"},
    }
    g["11"] = {
        "class_type": "WanFirstLastFrameToVideo",
        "inputs": {
            "positive": ["6", 0],
            "negative": ["7", 0],
            "vae": ["8", 0],
            "clip_vision_start_image": ["13", 0],
            "clip_vision_end_image": ["14", 0],
            "start_image": ["10", 0],
            "end_image": ["12", 0],
            "width": int(width),
            "height": int(height),
            "length": int(frames),
            "batch_size": 1,
        },
    }
    low = _wan_samplers(g, ["11", 0], ["11", 1], ["11", 2], steps, split, seed, cfg)
    g["22"] = {"class_type": "VAEDecode", "inputs": {"samples": [low, 0], "vae": ["8", 0]}}
    return _finish_video(g, "22", probe, prefix, fps)


def _wan_frames(frames: int) -> int:
    """Wan wants ``4n+1`` frames; snap instead of letting the server pick."""
    n = max(5, int(frames))
    rest = (n - 1) % 4
    if rest:
        n += (4 - rest) if rest >= 2 else -rest
    return n


def _lora_common(
    m: Mapping[str, str],
    high_key: str,
    low_key: str,
    lora_high: str,
    lora_low: str,
    prompt: str,
    negative: str,
    shift: float = 5.0,
) -> Dict[str, Any]:
    """UNET -> 4-step lightx2v LoRA -> ModelSamplingSD3, per expert.

    ``shift`` comes from Comfy's official templates and is NOT interchangeable:
    the lightx2v 4-step t2v / i2v / flf2v graphs use **5.0**, the Fun Camera
    4-step graph uses **8.0**, and the plain 20-step graphs use 8.0.  A wrong
    shift here does not error -- it renders a flat field.
    """
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": m[high_key], "weight_dtype": "default"}},
        "2": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["1", 0], "lora_name": m[lora_high], "strength_model": 1.0},
        },
        "3": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["2", 0], "shift": float(shift)}},
        "4": {"class_type": "UNETLoader", "inputs": {"unet_name": m[low_key], "weight_dtype": "default"}},
        "5": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["4", 0], "lora_name": m[lora_low], "strength_model": 1.0},
        },
        "6": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["5", 0], "shift": float(shift)}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": m["clip"], "type": "wan"}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["7", 0]}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["7", 0]}},
        "10": {"class_type": "VAELoader", "inputs": {"vae_name": m["vae"]}},
    }


def wan_camera(
    image_name: str,
    prompt: str,
    negative: str = "",
    width: int = 832,
    height: int = 480,
    frames: int = 81,
    steps: int = 4,
    split: int = 2,
    seed: int = 0,
    cfg: float = 1.0,
    move: str = "static",
    speed: float = 1.0,
    mdl: Optional[Mapping[str, str]] = None,
    probe: Optional[Mapping[str, Any]] = None,
    prefix: str = "cinema/wan_camera",
    fps: int = 16,
) -> Dict[str, Any]:
    """Wan 2.2 Fun Camera - our rig drives ``WanCameraEmbedding``.

    Derived from Comfy's official ``video_wan2_2_14B_fun_camera`` template:
    two experts, the 4-step lightx2v i2v LoRAs on each, ModelSamplingSD3 shift 8,
    euler/simple, cfg 1.  The embedding's width/height/length outputs feed the
    video node so the geometry can never disagree.
    """
    m = models(mdl)
    frames = _wan_frames(frames)
    width, height = _even16(width), _even16(height)
    g = _lora_common(
        m, "wan_camera_high", "wan_camera_low", "lora_i2v_high", "lora_i2v_low", prompt, negative,
        shift=8.0,
    )
    g["11"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    g["12"] = {
        "class_type": "WanCameraEmbedding",
        "inputs": {
            "camera_pose": camera_pose(move),
            "width": width,
            "height": height,
            "length": frames,
            "speed": float(speed),
            "fx": 0.5,
            "fy": 0.5,
            "cx": 0.5,
            "cy": 0.5,
        },
    }
    g["13"] = {
        "class_type": "WanCameraImageToVideo",
        "inputs": {
            "positive": ["8", 0],
            "negative": ["9", 0],
            "vae": ["10", 0],
            "start_image": ["11", 0],
            "camera_conditions": ["12", 0],
            "width": ["12", 1],
            "height": ["12", 2],
            "length": ["12", 3],
            "batch_size": 1,
        },
    }
    low = _wan_samplers(
        g, ["13", 0], ["13", 1], ["13", 2], steps, split, seed, cfg,
        high_model=["3", 0], low_model=["6", 0], end_at=steps,
    )
    g["22"] = {"class_type": "VAEDecode", "inputs": {"samples": [low, 0], "vae": ["10", 0]}}
    return _finish_video(g, "22", probe, prefix, fps)


def wan_lora_t2v(
    prompt: str,
    negative: str = "",
    width: int = 832,
    height: int = 480,
    frames: int = 81,
    steps: int = 4,
    split: int = 2,
    seed: int = 0,
    cfg: float = 1.0,
    mdl: Optional[Mapping[str, str]] = None,
    probe: Optional[Mapping[str, Any]] = None,
    prefix: str = "cinema/wan_t2v",
    fps: int = 16,
) -> Dict[str, Any]:
    """Wan 2.2 T2V with the lightx2v 4-step v1.1 LoRAs (official template)."""
    m = models(mdl)
    frames = _wan_frames(frames)
    width, height = _even16(width), _even16(height)
    g = _lora_common(m, "wan_t2v_high", "wan_t2v_low", "lora_t2v_high", "lora_t2v_low", prompt, negative)
    g["11"] = {
        "class_type": choose_latent_node(probe),
        "inputs": {"width": width, "height": height, "length": frames, "batch_size": 1},
    }
    low = _wan_samplers(
        g, ["8", 0], ["9", 0], ["11", 0], steps, split, seed, cfg,
        high_model=["3", 0], low_model=["6", 0], end_at=steps,
    )
    g["22"] = {"class_type": "VAEDecode", "inputs": {"samples": [low, 0], "vae": ["10", 0]}}
    return _finish_video(g, "22", probe, prefix, fps)


def wan_lora_i2v(
    image_name: str,
    prompt: str,
    negative: str = "",
    width: int = 832,
    height: int = 480,
    frames: int = 81,
    steps: int = 4,
    split: int = 2,
    seed: int = 0,
    cfg: float = 1.0,
    mdl: Optional[Mapping[str, str]] = None,
    probe: Optional[Mapping[str, Any]] = None,
    prefix: str = "cinema/wan_i2v",
    fps: int = 16,
) -> Dict[str, Any]:
    """Wan 2.2 I2V with the lightx2v 4-step v1 LoRAs (official template)."""
    m = models(mdl)
    frames = _wan_frames(frames)
    width, height = _even16(width), _even16(height)
    g = _lora_common(m, "wan_i2v_high", "wan_i2v_low", "lora_i2v_high", "lora_i2v_low", prompt, negative)
    g["11"] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": m["clip_vision"]}}
    g["12"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    # see wan_lora_i2v docstring: the video node wants the ENCODE, not the loader
    g["14"] = {
        "class_type": "CLIPVisionEncode",
        "inputs": {"clip_vision": ["11", 0], "image": ["12", 0], "crop": "none"},
    }
    g["13"] = {
        "class_type": "WanImageToVideo",
        "inputs": {
            "positive": ["8", 0],
            "negative": ["9", 0],
            "vae": ["10", 0],
            "clip_vision_output": ["14", 0],
            "start_image": ["12", 0],
            "width": width,
            "height": height,
            "length": frames,
            "batch_size": 1,
        },
    }
    low = _wan_samplers(
        g, ["13", 0], ["13", 1], ["13", 2], steps, split, seed, cfg,
        high_model=["3", 0], low_model=["6", 0], end_at=steps,
    )
    g["22"] = {"class_type": "VAEDecode", "inputs": {"samples": [low, 0], "vae": ["10", 0]}}
    return _finish_video(g, "22", probe, prefix, fps)


def _even16(value: int) -> int:
    return max(16, int(round(int(value) / 16.0)) * 16)


def _mult64(value: int) -> int:
    return max(64, int(round(int(value) / 64.0)) * 64)


def _ltx_frames(frames: int) -> int:
    """LTX wants ``8n+1`` frames."""
    n = max(9, int(frames))
    return n - ((n - 1) % 8)


def ltx_video(
    prompt: str,
    negative: str = "",
    width: int = 768,
    height: int = 512,
    frames: int = 49,
    steps: int = 12,
    seed: int = 0,
    cfg: float = 3.0,
    image_name: Optional[str] = None,
    mdl: Optional[Mapping[str, str]] = None,
    probe: Optional[Mapping[str, Any]] = None,
    prefix: str = "cinema/ltx",
    fps: int = 25,
) -> Dict[str, Any]:
    """LTX-2 single-stage graph, derived from Comfy's official LTX-2 template.

    The official template is two-stage (spatial upscaler + audio VAE).  This keeps
    the audio latent (an LTX-2 model expects the audio-video layout) but samples
    once at the prompt resolution - a draft path that validates on the core nodes
    Comfy Cloud ships.
    """
    m = models(mdl)
    width, height = _mult64(width), _mult64(height)
    frames = _ltx_frames(frames)
    g: Dict[str, Any] = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": m["ltx_ckpt"]}},
        "2": {
            "class_type": "LTXAVTextEncoderLoader",
            "inputs": {
                "text_encoder": m["ltx_text_encoder"],
                "ckpt_name": m["ltx_ckpt"],
                "device": "default",
            },
        },
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 0]}},
        "5": {
            "class_type": "LTXVConditioning",
            "inputs": {"positive": ["3", 0], "negative": ["4", 0], "frame_rate": float(fps)},
        },
        "6": {
            "class_type": "EmptyLTXVLatentVideo",
            "inputs": {"width": width, "height": height, "length": frames, "batch_size": 1},
        },
        "7": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": m["ltx_ckpt"]}},
        "8": {
            "class_type": "LTXVEmptyLatentAudio",
            "inputs": {
                "audio_vae": ["7", 0],
                "frames_number": frames,
                "frame_rate": int(fps),
                "batch_size": 1,
            },
        },
        "9": {
            "class_type": "LTXVConcatAVLatent",
            "inputs": {"video_latent": ["6", 0], "audio_latent": ["8", 0]},
        },
    }
    latent: List[Any] = ["9", 0]
    if image_name:
        g["20"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        g["21"] = {
            "class_type": "LTXVPreprocess",
            "inputs": {"image": ["20", 0], "img_compression": 33},
        }
        g["22"] = {
            "class_type": "LTXVImgToVideoInplace",
            "inputs": {
                "vae": ["1", 2],
                "image": ["21", 0],
                "latent": ["9", 0],
                "strength": 1.0,
                "bypass": False,
            },
        }
        latent = ["22", 0]
    g["10"] = {
        "class_type": "CFGGuider",
        "inputs": {"model": ["1", 0], "positive": ["5", 0], "negative": ["5", 1], "cfg": float(cfg)},
    }
    g["11"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}}
    g["12"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
    g["13"] = {
        "class_type": "LTXVScheduler",
        "inputs": {
            "steps": int(steps),
            "max_shift": 2.05,
            "base_shift": 0.95,
            "stretch": True,
            "terminal": 0.1,
            "latent": latent,
        },
    }
    g["14"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["12", 0],
            "guider": ["10", 0],
            "sampler": ["11", 0],
            "sigmas": ["13", 0],
            "latent_image": latent,
        },
    }
    g["15"] = {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["14", 0]}}
    g["16"] = {"class_type": "VAEDecode", "inputs": {"samples": ["15", 0], "vae": ["1", 2]}}
    return _finish_video(g, "16", probe, prefix, fps)


def build_shot(
    mode: str,
    prompt: str,
    negative: str = "",
    quality: str = "draft",
    seed: int = 0,
    image_name: Optional[str] = None,
    end_image_name: Optional[str] = None,
    mdl: Optional[Mapping[str, str]] = None,
    probe: Optional[Mapping[str, Any]] = None,
    prefix: Optional[str] = None,
    engine: Optional[str] = None,
    move: str = "static",
    speed: float = 1.0,
    width: Optional[int] = None,
    height: Optional[int] = None,
    frames: Optional[int] = None,
    steps: Optional[int] = None,
) -> Dict[str, Any]:
    """One entry point for the API + CLI.  ``mode`` in MODES (reshoot == i2v).

    ``engine`` picks the builder; it defaults per mode (``sdxl`` for a keyframe,
    ``wan_camera`` for anything that starts from an image, ``wan_t2v`` for text,
    ``wan_flf2v`` for two frames).  ``width``/``height``/``frames``/``steps``
    override the quality preset.
    """
    if mode not in MODES:
        raise GraphError("unknown mode %r (choose one of: %s)" % (mode, ", ".join(MODES)))
    engine = engine or default_engine(mode)
    if engine not in ENGINES:
        raise GraphError("unknown engine %r (choose one of: %s)" % (engine, ", ".join(ENGINES)))
    p = preset(quality, mode)
    if width:
        p["width"] = int(width)
    if height:
        p["height"] = int(height)
    if frames:
        p["frames"] = int(frames)
    prefix = prefix or ("cinema/%s/%s" % (mode, seed))

    if engine == "sdxl" or (mode == "keyframe" and engine == default_engine(mode)):
        return keyframe(
            prompt,
            negative=negative,
            width=p["width"],
            height=p["height"],
            steps=28,
            seed=seed,
            mdl=mdl,
            prefix=prefix,
        )
    if mode == "keyframe":
        raise GraphError("a keyframe still only uses the sdxl engine")

    # The lightx2v LoRAs are *4-step* distillations: 4 steps draft, 8 for final.
    lora_steps = 4 if quality != "final" else 8
    if engine in ("wan_camera", "wan_t2v", "wan_i2v"):
        steps = int(steps or lora_steps)
    else:
        steps = int(steps or p["steps"])
    split = max(1, steps // 2)
    if engine == "ltx":
        return ltx_video(
            prompt, negative, width=p["width"], height=p["height"], frames=p["frames"],
            steps=steps, seed=seed, cfg=3.0, image_name=image_name, mdl=mdl, probe=probe,
            prefix=prefix, fps=p["fps"],
        )
    if engine == "wan_flf2v":
        if not image_name:
            raise GraphError("mode %r needs a start image" % mode)
        if not end_image_name:
            raise GraphError("flf2v needs an end image")
        return flf2v(
            image_name, end_image_name, prompt, negative, width=p["width"], height=p["height"],
            frames=p["frames"], steps=steps, split=split, seed=seed, mdl=mdl,
            probe=probe, prefix=prefix, fps=p["fps"],
        )
    if engine == "wan_t2v":
        return wan_lora_t2v(
            prompt, negative, width=p["width"], height=p["height"], frames=p["frames"],
            steps=steps, split=split, seed=seed, mdl=mdl, probe=probe, prefix=prefix,
            fps=p["fps"],
        )
    if not image_name:
        raise GraphError("mode %r needs a start image" % mode)
    if engine == "wan_camera":
        return wan_camera(
            image_name, prompt, negative, width=p["width"], height=p["height"],
            frames=p["frames"], steps=steps, split=split, seed=seed, move=move,
            speed=speed, mdl=mdl, probe=probe, prefix=prefix, fps=p["fps"],
        )
    return wan_lora_i2v(
        image_name, prompt, negative, width=p["width"], height=p["height"], frames=p["frames"],
        steps=steps, split=split, seed=seed, mdl=mdl, probe=probe, prefix=prefix, fps=p["fps"],
    )


# ---------------------------------------------------------------------------
# validation helpers used by tests and the export command
# ---------------------------------------------------------------------------
def iter_links(graph: Mapping[str, Any]) -> Iterable[Tuple[str, str, Any]]:
    """Yield (node_id, input_name, link) for every [node_id, slot] link."""
    for node_id, node in graph.items():
        if node_id.startswith("_") or not isinstance(node, dict):
            continue
        for name, value in (node.get("inputs") or {}).items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                yield node_id, name, value


def api_graph(graph: Mapping[str, Any]) -> Dict[str, Any]:
    """Strip the builder's ``_``-prefixed annotations (``_output``) for /prompt."""
    return {k: v for k, v in graph.items() if not str(k).startswith("_")}


def validate_graph(graph: Mapping[str, Any]) -> List[str]:
    """Return a list of structural problems (empty = valid)."""
    problems: List[str] = []
    for node_id, node in graph.items():
        if node_id.startswith("_"):
            continue
        if not isinstance(node, dict) or "class_type" not in node:
            problems.append("node %s has no class_type" % node_id)
    for node_id, name, link in iter_links(graph):
        if link[0] not in graph:
            problems.append("node %s input %s points at missing node %s" % (node_id, name, link[0]))
    return problems


__all__ = [
    "DEFAULT_MODELS",
    "CLOUD_MODELS",
    "PRESETS",
    "MODES",
    "ENGINES",
    "MOTION_ENGINES",
    "CAMERA_POSES",
    "MOVE_POSE",
    "SPEED_VALUES",
    "default_engine",
    "camera_pose",
    "wan_camera",
    "wan_lora_t2v",
    "wan_lora_i2v",
    "ltx_video",
    "GraphError",
    "models",
    "preset",
    "normalize_probe",
    "choose_video_output",
    "choose_latent_node",
    "video_output_nodes",
    "keyframe",
    "t2v",
    "i2v",
    "flf2v",
    "build_shot",
    "api_graph",
    "validate_graph",
]
