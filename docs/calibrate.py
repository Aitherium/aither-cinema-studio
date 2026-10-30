"""Calibrate ComfyUI render time for Aither Cinema Studio (clean-room, MIT).

Submits two real graphs to a live ComfyUI, times them to completion, downloads the
artifacts, and folds the timings into docs/comfy-probe.json under "timings".

  1. wan_t2v_draft  — Wan 2.2 t2v two-expert (high/low noise) MoE, 480x480, 33 frames,
                      8 steps split 4, euler/simple, cfg 3.5.
  2. sdxl_keyframe  — RealVisXL keyframe, 768x768, 28 steps dpmpp_2m karras cfg 5.

Run:  python docs/calibrate.py [--only wan|sdxl] [--no-merge]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

BASE = os.environ.get("COMFY_BASE_URL", "https://127.0.0.1:8188")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAL_DIR = os.path.join(ROOT, "docs", "calibration")
PROBE = os.path.join(ROOT, "docs", "comfy-probe.json")

WAN_HIGH = "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
WAN_LOW = "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"
WAN_CLIP = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
WAN_VAE = "wan_2.1_vae.safetensors"
SDXL_CKPT = "RealVisXL_V4.0.safetensors"


# --------------------------------------------------------------------------- graphs
def graph_wan_t2v(prompt: str, negative: str, *, width: int = 480, height: int = 480,
                  frames: int = 33, steps: int = 8, split: int = 4, seed: int = 42,
                  prefix: str = "cinema_calib/wan_t2v",
                  weight_dtype: str = "default") -> dict:
    """Wan 2.2 t2v, two experts. HIGH runs [0, split), LOW runs [split, steps).

    Running ONE expert for every step yields noise: the second expert needs the
    leftover noise the first hands it.
    """
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": WAN_HIGH, "weight_dtype": weight_dtype}},
        "2": {"class_type": "ModelSamplingSD3",
              "inputs": {"model": ["1", 0], "shift": 8.0}},
        "3": {"class_type": "UNETLoader",
              "inputs": {"unet_name": WAN_LOW, "weight_dtype": weight_dtype}},
        "4": {"class_type": "ModelSamplingSD3",
              "inputs": {"model": ["3", 0], "shift": 8.0}},
        "5": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": WAN_CLIP, "type": "wan"}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["5", 0], "text": prompt}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["5", 0], "text": negative}},
        "8": {"class_type": "VAELoader", "inputs": {"vae_name": WAN_VAE}},
        "9": {"class_type": "EmptyHunyuanLatentVideo",
              "inputs": {"width": width, "height": height, "length": frames,
                         "batch_size": 1}},
        "10": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["2", 0], "add_noise": "enable",
                          "noise_seed": seed, "steps": steps, "cfg": 3.5,
                          "sampler_name": "euler", "scheduler": "simple",
                          "positive": ["6", 0], "negative": ["7", 0],
                          "latent_image": ["9", 0], "start_at_step": 0,
                          "end_at_step": split,
                          "return_with_leftover_noise": "enable"}},
        "11": {"class_type": "KSamplerAdvanced",
               "inputs": {"model": ["4", 0], "add_noise": "disable",
                          "noise_seed": seed, "steps": steps, "cfg": 3.5,
                          "sampler_name": "euler", "scheduler": "simple",
                          "positive": ["6", 0], "negative": ["7", 0],
                          "latent_image": ["10", 0], "start_at_step": split,
                          "end_at_step": steps,
                          "return_with_leftover_noise": "disable"}},
        "12": {"class_type": "VAEDecode",
               "inputs": {"samples": ["11", 0], "vae": ["8", 0]}},
        "13": {"class_type": "VHS_VideoCombine",
               "inputs": {"images": ["12", 0], "frame_rate": 12.0, "loop_count": 0,
                          "filename_prefix": prefix, "format": "video/h264-mp4",
                          "pingpong": False, "save_output": True}},
    }


def graph_sdxl_keyframe(prompt: str, negative: str, *, width: int = 768,
                        height: int = 768, steps: int = 28, seed: int = 42,
                        prefix: str = "cinema_calib/keyframe") -> dict:
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": SDXL_CKPT}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": prompt}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["1", 1], "text": negative}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "seed": seed, "steps": steps,
                         "cfg": 5.0, "sampler_name": "dpmpp_2m",
                         "scheduler": "karras", "positive": ["2", 0],
                         "negative": ["3", 0], "latent_image": ["4", 0],
                         "denoise": 1.0}},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": prefix}},
    }


# --------------------------------------------------------------------------- comfy io
def client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE, verify=False,
        timeout=httpx.Timeout(connect=15.0, read=300.0, write=900.0, pool=15.0))


def submit(c: httpx.Client, graph: dict, client_id: str) -> str:
    r = c.post("/prompt", json={"prompt": graph, "client_id": client_id})
    if r.status_code != 200:
        raise RuntimeError(f"/prompt {r.status_code}: {r.text[:2000]}")
    body = r.json()
    if body.get("node_errors"):
        raise RuntimeError(f"node_errors: {json.dumps(body['node_errors'])[:2000]}")
    return body["prompt_id"]


def wait_done(c: httpx.Client, prompt_id: str, timeout_s: float = 2400.0) -> dict:
    """Block until the prompt leaves the queue, then return its history entry."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        h = c.get(f"/history/{prompt_id}").json()
        entry = h.get(prompt_id)
        if entry:
            status = entry.get("status") or {}
            if status.get("status_str") == "error" or status.get("completed"):
                return entry
            if entry.get("outputs"):
                return entry
        time.sleep(2.0)
    raise TimeoutError(f"prompt {prompt_id} did not finish in {timeout_s:.0f}s")


def collect_media(entry: dict) -> list[dict]:
    """Every file the run produced, across images/gifs/videos/audio keys."""
    out: list[dict] = []
    for node_out in (entry.get("outputs") or {}).values():
        for key in ("images", "gifs", "videos", "audio"):
            for item in node_out.get(key) or []:
                if isinstance(item, dict) and item.get("filename"):
                    out.append(item)
    return out


def download(c: httpx.Client, item: dict, job_dir: str) -> str:
    os.makedirs(job_dir, exist_ok=True)
    params = {"filename": item["filename"], "subfolder": item.get("subfolder", ""),
              "type": item.get("type", "output")}
    r = c.get("/view", params=params)
    r.raise_for_status()
    name = os.path.basename(item["filename"])
    path = os.path.join(job_dir, name)
    with open(path, "wb") as fh:
        fh.write(r.content)
    return path


def run_job(c: httpx.Client, name: str, graph: dict, note: str) -> dict:
    print(f"[calibrate] {name}: submitting ...", flush=True)
    t0 = time.time()
    pid = submit(c, graph, f"cinema-{name}")
    entry = wait_done(c, pid)
    elapsed = time.time() - t0
    status = (entry.get("status") or {}).get("status_str")
    job_dir = os.path.join(CAL_DIR, name)
    files = []
    for item in collect_media(entry):
        try:
            files.append(os.path.relpath(download(c, item, job_dir), ROOT))
        except Exception as exc:  # noqa: BLE001 - a failed download is reported
            files.append(f"DOWNLOAD FAILED: {type(exc).__name__}: {exc}")
    ok = status == "success" and any(not f.startswith("DOWNLOAD") for f in files)
    print(f"[calibrate] {name}: {elapsed:.1f}s status={status} files={len(files)}",
          flush=True)
    return {
        "name": name,
        "note": note,
        "prompt_id": pid,
        "seconds": round(elapsed, 1),
        "comfy_status": status,
        "ok": ok,
        "outputs": files,
        "graph": {k: v["class_type"] for k, v in graph.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["wan", "sdxl"], default=None)
    ap.add_argument("--no-merge", action="store_true")
    ap.add_argument("--wan-weight-dtype", default="default",
                    help="UNETLoader weight_dtype for the Wan experts. Use fp8_e4m3fn "
                         "to keep the 14B weights single-byte and avoid a host OOM "
                         "when little system RAM is free.")
    args = ap.parse_args()

    prompt = ("A lone figure in a wet raincoat stands on a neon-lit city street at night, "
              "the camera slowly dollies in toward the face, 35mm lens, cinematic "
              "anamorphic, shallow depth of field")
    negative = "blurry, low quality, distorted, watermark, text, extra limbs"

    jobs: list[dict] = []
    with client() as c:
        try:
            stats = c.get("/system_stats").json()
        except Exception as exc:  # noqa: BLE001
            print(f"ComfyUI unreachable at {BASE}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 2
        print(f"[calibrate] comfy {stats.get('system', {}).get('comfyui_version')}")

        if args.only in (None, "wan"):
            g = graph_wan_t2v(prompt, negative,
                              weight_dtype=args.wan_weight_dtype)
            jobs.append(run_job(c, "wan_t2v_draft", g,
                                "Wan 2.2 t2v two-expert, 480x480, 33f, 8 steps split 4, "
                                "euler/simple, cfg 3.5"))
        if args.only in (None, "sdxl"):
            g = graph_sdxl_keyframe(prompt, negative)
            jobs.append(run_job(c, "sdxl_keyframe", g,
                                "RealVisXL V4.0, 768x768, 28 steps dpmpp_2m karras, cfg 5"))

    print(json.dumps([{k: j[k] for k in ("name", "seconds", "ok")} for j in jobs],
                     indent=2))

    if not args.no_merge:
        merge_timings(jobs)
    return 0 if all(j["ok"] for j in jobs) else 1


def merge_timings(jobs: list[dict]) -> None:
    probe: dict = {}
    if os.path.exists(PROBE):
        with open(PROBE, encoding="utf-8") as fh:
            probe = json.load(fh)
    loras = (probe.get("models", {}).get("loras") or [])
    if not isinstance(loras, list):
        loras = []
    light = [n for n in loras if "lightx2v" in n.lower()]
    prev = (probe.get("timings") or {}).get("jobs") or []
    by_name = {j["name"]: j for j in prev if isinstance(j, dict) and "name" in j}
    for j in jobs:
        by_name[j["name"]] = j
    merged = list(by_name.values())
    probe["timings"] = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "jobs": merged,
        "summary": {j["name"]: j["seconds"] for j in merged},
    }
    probe["step_cutting_loras"] = {
        "wan_lightx2v_4step": light,
        "note": ("wan2.2 t2v lightx2v v1.1 4-step LoRAs for BOTH experts are present; "
                 "they cut a draft render from 8 steps to ~4 (split 2/2)."),
    }
    with open(PROBE, "w", encoding="utf-8") as fh:
        json.dump(probe, fh, indent=2)
        fh.write("\n")
    print("[calibrate] merged timings into", PROBE)


if __name__ == "__main__":
    sys.exit(main())
