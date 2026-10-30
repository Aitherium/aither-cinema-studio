"""ComfyUI-CinemaRig - two nodes for directing a film on a ComfyUI set.

* ``CinemaCameraRig``  - camera move / lens / speed / framing -> a prompt clause
  string, using the exact same vocabulary table as the studio backend
  (``camera_vocab.json`` is shipped in both trees and asserted byte-identical).
* ``CinemaFilmGrade``  - an IMAGE batch -> graded IMAGE batch.  Each frame is
  written to PNG, run through the grading engine's colour-lookup command, and
  blended back by ``strength``.

Clean-room: everything here is written for this repo.  The only external
dependency at runtime is the grading CLI (``PHOTOCRAFT_BIN`` or a binary on
PATH); nothing else is imported from another project.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent

with (_HERE / "camera_vocab.json").open("r", encoding="utf-8") as _fh:
    VOCAB = json.load(_fh)

MOVES = list(VOCAB["moves"].keys())
LENSES = list(VOCAB["lenses"].keys())
SPEEDS = list(VOCAB["speeds"].keys())
FRAMINGS = list(VOCAB["framings"].keys())

LOOKS = [
    "none",
    "warm",
    "cool",
    "tealOrange",
    "bleachBypass",
    "fadedFilm",
    "dayForNight",
    "monoContrast",
    "crossProcess",
]

CLI_COMMAND = "image.adjustments.colorLookup"
CATEGORY = "Cinema"


def compile_prompt(prompt: str, move: str, lens: str, speed: str, framing: str) -> str:
    """Same compiler as the studio backend: subject, camera, lens, framing."""
    parts = []
    subject = (prompt or "").strip()
    if subject:
        parts.append(subject if subject[-1] in ".!?" else subject + ".")
    adverb = VOCAB["speeds"][speed]["adverb"]

    def clause(section, ident):
        template = VOCAB[section][ident]["clause"]
        return template.replace("{speed}", adverb) if "{speed}" in template else template

    parts.append(clause("moves", move) + ".")
    parts.append(clause("lenses", lens) + ".")
    parts.append(clause("framings", framing) + ".")
    return " ".join(parts)


def find_grade_bin():
    """PHOTOCRAFT_BIN, then PATH, then the private install under ~/.aither."""
    env = os.environ.get("PHOTOCRAFT_BIN")
    if env and Path(env).is_file():
        return str(Path(env).expanduser())
    found = shutil.which("photocraft-cli")
    if found:
        return found
    home = Path.home() / ".aither" / "photocraft"
    for name in ("photocraft-cli", "photocraft-cli.exe"):
        candidate = home / name
        if candidate.is_file():
            return str(candidate)
    return None


def validate_look(look: str) -> str:
    value = (look or "none").strip()
    if value not in LOOKS:
        raise ValueError("unknown grade look %r (choose one of: %s)" % (look, ", ".join(LOOKS)))
    return value


class CinemaCameraRig:
    """Build the prompt text for a camera rig (identical table to the backend)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "a lone figure walks across a rain-soaked neon street",
                    },
                ),
                "move": (MOVES, {"default": "dolly_in"}),
                "lens": (LENSES, {"default": "35mm"}),
                "speed": (SPEEDS, {"default": "slow"}),
                "framing": (FRAMINGS, {"default": "medium"}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "compile"
    CATEGORY = CATEGORY

    def compile(self, prompt, move, lens, speed, framing):
        return (compile_prompt(prompt, move, lens, speed, framing),)


class CinemaFilmGrade:
    """Grade an IMAGE batch with a colour lookup, blended by strength."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "look": (LOOKS, {"default": "tealOrange"}),
                "strength": (
                    "FLOAT",
                    {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05},
                ),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "grade"
    CATEGORY = CATEGORY

    def grade(self, images, look, strength):
        import torch
        from PIL import Image

        look = validate_look(look)
        strength = float(strength)
        if look == "none" or strength <= 0.0:
            return (images,)
        binary = find_grade_bin()
        if not binary:
            raise RuntimeError(
                "grading CLI not found: set PHOTOCRAFT_BIN or put photocraft-cli on PATH"
            )

        frames = []
        with tempfile.TemporaryDirectory(prefix="cinema-grade-") as tmp:
            tmpdir = Path(tmp)
            for index, frame in enumerate(images):
                array = (frame.clamp(0.0, 1.0) * 255.0).round().to(torch.uint8).cpu().numpy()
                original = Image.fromarray(array, mode="RGB")
                src = tmpdir / ("frame_%05d.png" % index)
                dst = tmpdir / ("graded_%05d.png" % index)
                original.save(src, format="PNG")
                params = '{"lut":"%s"}' % look
                proc = subprocess.run(
                    [
                        binary,
                        "run",
                        str(src),
                        "--cmd",
                        CLI_COMMAND,
                        "--params",
                        params,
                        "--out",
                        str(dst),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if proc.returncode != 0 or not dst.exists():
                    raise RuntimeError(
                        "grade failed on frame %d (%s): %s"
                        % (index, proc.returncode, (proc.stderr or proc.stdout or "")[-400:])
                    )
                graded = Image.open(dst).convert("RGB")
                if graded.size != original.size:
                    graded = graded.resize(original.size)
                if strength < 1.0:
                    graded = Image.blend(original, graded, strength)
                frames.append(torch.from_numpy(_to_array(graded)).float().div_(255.0))

        batch = torch.stack(frames, dim=0)
        if images.is_cuda:  # keep the batch on the same device as the input
            batch = batch.to(images.device)
        return (batch,)


def _to_array(image):
    import numpy as np

    return np.array(image, dtype=np.uint8)


NODE_CLASS_MAPPINGS = {
    "CinemaCameraRig": CinemaCameraRig,
    "CinemaFilmGrade": CinemaFilmGrade,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CinemaCameraRig": "Cinema Camera Rig",
    "CinemaFilmGrade": "Cinema Film Grade",
}

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "compile_prompt",
    "CinemaCameraRig",
    "CinemaFilmGrade",
]
