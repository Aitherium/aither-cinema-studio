# Comfy API Build — Aither Cinema Studio

A [Comfy API Build](https://docs.comfy.org) is a ComfyUI image the Comfy Cloud
builder assembles from a **spec file** — a base ComfyUI version, a list of model
weights with pinned hashes, and optional custom-node packs. Push the spec, cut a
release, and the four graphs in `../workflows/` run on Comfy Cloud hardware with
no local GPU.

`comfy-build.yaml` in this folder is that spec, and it validates.

```
comfy build validate comfy-build            -> ok: true, folder_case_checked: false
comfy build validate comfy-build --remote   -> ok: true, folder_case_checked: true
comfy build push comfy-build --dry-run      -> ok: true, upload_count: 0
```

All three of those are **free and offline-or-read-only**. Nothing in this folder
has been pushed or released.

## What the build contains

Base image: `v0.43.2` (newest tag on `comfyanonymous/ComfyUI` at authoring time;
the builder's default CUDA/Python image, `cuda130-py312`, is left unset on
purpose so the catalog default applies).

| type | filename | size | loaded by |
|---|---|---|---|
| `diffusion_models` | `wan2.2_i2v_high_noise_14B_fp8_scaled` | 14.3 GB | `i2v.json`, `flf2v.json` |
| `diffusion_models` | `wan2.2_i2v_low_noise_14B_fp8_scaled` | 14.3 GB | `i2v.json`, `flf2v.json` |
| `diffusion_models` | `wan2.2_t2v_high_noise_14B_fp8_scaled` | 14.3 GB | `t2v.json` |
| `diffusion_models` | `wan2.2_t2v_low_noise_14B_fp8_scaled` | 14.3 GB | `t2v.json` |
| `text_encoders` | `umt5_xxl_fp8_e4m3fn_scaled` | 6.7 GB | every video graph |
| `vae` | `wan_2.1_vae` | 0.3 GB | every video graph |
| `clip_vision` | `CLIP-ViT-H-14-laion2B-s32B-b79K` | 2.5 GB | `i2v.json`, `flf2v.json` |
| `checkpoints` | `RealVisXL_V4.0` | 6.9 GB | `keyframe.json` |

That is 8 files, **73.6 GB**. Every `sha256` in the spec came from
`comfy build refs resolve <filename>` with `verified: true`; re-run that command
instead of editing a digest by hand.

LTX 2.3 is **not** in the active list. Two verified candidates are commented
into the spec (`ltx-2.3-22b-distilled-fp8` 29.5 GB, and
`ltx-2.3-22b-distilled_transformer_only_fp8_scaled` 23.5 GB) — 53 GB that no
shipped graph loads. Uncomment them when an LTX graph exists.

## The node pack

`../comfy_nodes/ComfyUI-CinemaRig` (the `CinemaCameraRig` and `CinemaFilmGrade`
nodes) is not in the build yet. A `customNodes` entry can only name a public
GitHub **repository**, a registry version, or an uploaded blob — and a
repository source clones the repo root, with no way to point at a subdirectory.
The studio repo is not public yet, so there is nothing honest to pin. The
commented block in the spec shows the exact entry to add once
`ComfyUI-CinemaRig` is published as its own repository; the helper text is in
`comfy build refs resolve` / `comfy skills show comfy-build-authoring`.

Without the pack the build still renders: Wan 2.2, CLIP and VAE loaders are
core ComfyUI nodes, and the studio's backend compiles the camera prompt itself
(`server/cinema/camera.py`). The pack only matters if the graphs are edited to
use the two cinema nodes.

## Commands

Free — safe to run any time, no build slot:

```bash
pip install "comfy-cli>=1.24"
comfy build validate comfy-build
comfy build validate comfy-build --remote        # needs COMFY_CLOUD_API_KEY
comfy build push comfy-build --dry-run
```

The owner-only steps. **`push` spends a build slot and `release` publishes an
image** — neither was run here:

```bash
export COMFY_CLOUD_API_KEY=...                   # workspace API key, never committed
comfy build push comfy-build                     # first push fills id + syncedRevision
comfy build release create --target linux/nvidia --watch
```

## Notes that cost a release

- `sha256` and `sourceUri` are pinned together. The resolver returns several
  candidate repos for some files (three for `umt5_xxl…`, five for `wan_2.1_vae`);
  the spec keeps the `Comfy-Org` repackaged copy, which is the one ComfyUI's own
  example graphs document.
- A `gitRef` on a `repository` must be a 40-hex commit, never a branch — a branch
  resolves at cut time, so two cuts of one spec can ship different code.
- `modelPolicy` / `customNodePolicy` are a record the release carries, not a
  restriction the platform enforces. Nothing here uses them.
- `environment` is provenance written by `comfy build init`; the builder ignores
  it. There is no local ComfyUI install on this machine, so the spec was written
  by hand rather than scanned — which also means it carries no `pipDependencies`.
