"""Probe a running ComfyUI and record the facts the Cinema Studio builders need.

Clean-room code for Aither Cinema Studio (MIT). No code copied from any other tree.

Writes docs/comfy-probe.json:
  version, classes (exists -> required/optional input name+type), model folders,
  expected-file visibility, and (if present) the lora folder.

Run:  python docs/probe_comfy.py [--out docs/comfy-probe.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

BASE = os.environ.get("COMFY_BASE_URL", "https://127.0.0.1:8188")

# Classes the studio's graph builders and the contract depend on.
CLASSES = [
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "CLIPVisionLoader",
    "CLIPVisionEncode",
    "ModelSamplingSD3",
    "WanImageToVideo",
    "WanFirstLastFrameToVideo",
    "EmptyHunyuanLatentVideo",
    "Wan22ImageToVideoLatent",
    "KSamplerAdvanced",
    "KSampler",
    "CheckpointLoaderSimple",
    "VAEDecode",
    "CreateVideo",
    "SaveVideo",
    "VHS_VideoCombine",
    "SaveAnimatedWEBP",
    "SaveImage",
    "LoadImage",
]

FOLDERS = ["diffusion_models", "text_encoders", "vae", "clip_vision", "checkpoints", "loras"]

# Filenames the contract assumes are present inside the container.
EXPECTED = {
    "diffusion_models": [
        "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
    ],
    "text_encoders": ["umt5_xxl_fp8_e4m3fn_scaled.safetensors"],
    "vae": ["wan_2.1_vae.safetensors"],
    "clip_vision": ["CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"],
    "checkpoints": ["RealVisXL_V4.0.safetensors"],
}


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE, verify=False, timeout=60.0)


def _fields(spec: dict) -> dict:
    """Turn ComfyUI's {name: [type, opts]} into {name: type} plus a few useful opts."""
    out: dict = {}
    for name, decl in (spec or {}).items():
        if not isinstance(decl, (list, tuple)) or not decl:
            out[name] = {"type": str(decl)}
            continue
        t = decl[0]
        if isinstance(t, list):          # an enum of choices -> a COMBO
            out[name] = {"type": "COMBO", "count": len(t), "values": t}
        else:
            entry = {"type": str(t)}
            opts = decl[1] if len(decl) > 1 and isinstance(decl[1], dict) else {}
            if "default" in opts:
                entry["default"] = opts["default"]
            if opts.get("min") is not None:
                entry["min"] = opts["min"]
            if opts.get("max") is not None:
                entry["max"] = opts["max"]
            out[name] = entry
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("docs", "comfy-probe.json"))
    args = ap.parse_args()

    result: dict = {
        "base_url": BASE,
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "reachable": False,
    }

    with client() as c:
        try:
            stats = c.get("/system_stats").json()
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            result["error"] = f"{type(exc).__name__}: {exc}"
            print(json.dumps(result, indent=2))
            _write(args.out, result)
            return 2

        result["reachable"] = True
        system = stats.get("system", {}) or {}
        result["version"] = {
            "comfyui": system.get("comfyui_version"),
            "frontend": system.get("required_frontend_version"),
            "python": system.get("python_version"),
            "torch": system.get("pytorch_version"),
            "os": system.get("os"),
            "argv": system.get("argv"),
        }
        result["devices"] = [
            {"name": d.get("name"), "type": d.get("type"),
             "vram_total_gib": round((d.get("vram_total") or 0) / 2**30, 2),
             "vram_free_gib": round((d.get("vram_free") or 0) / 2**30, 2)}
            for d in (stats.get("devices") or [])
        ]

        # --- classes -------------------------------------------------------
        classes: dict = {}
        for name in CLASSES:
            try:
                r = c.get(f"/object_info/{name}")
                if r.status_code != 200:
                    classes[name] = {"exists": False, "http": r.status_code}
                    continue
                body = r.json()
                node = body.get(name)
                if not node:
                    classes[name] = {"exists": False, "http": 200, "note": "absent from body"}
                    continue
                inp = node.get("input", {}) or {}
                classes[name] = {
                    "exists": True,
                    "required": _fields(inp.get("required", {})),
                    "optional": _fields(inp.get("optional", {})),
                    "output": node.get("output"),
                    "output_node": bool(node.get("output_node")),
                }
            except Exception as exc:  # noqa: BLE001
                classes[name] = {"exists": None,
                                 "error": f"{type(exc).__name__}: {exc}"}
        result["classes"] = classes

        # --- models --------------------------------------------------------
        models: dict = {}
        for folder in FOLDERS:
            try:
                r = c.get(f"/models/{folder}")
                models[folder] = r.json() if r.status_code == 200 else {
                    "http": r.status_code, "files": []}
            except Exception as exc:  # noqa: BLE001
                models[folder] = {"error": f"{type(exc).__name__}: {exc}"}
        result["models"] = models

        # --- expected-file visibility -------------------------------------
        vis: dict = {}
        for folder, names in EXPECTED.items():
            listing = models.get(folder)
            have = listing if isinstance(listing, list) else []
            vis[folder] = {n: (n in have) for n in names}
        result["expected_visible"] = vis
        result["expected_all_visible"] = all(
            all(v.values()) for v in vis.values())

    _write(args.out, result)
    print(json.dumps({k: result[k] for k in
                      ("reachable", "version", "expected_all_visible")}, indent=2))
    print("wrote", args.out)
    return 0


def _write(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=False)
        fh.write("\n")


if __name__ == "__main__":
    sys.exit(main())
