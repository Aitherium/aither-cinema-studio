"""Camera rig -> prompt-clause compiler.

The vocabulary is data (``camera_vocab.json``, shipped in this package and
mirrored byte-for-byte into the ComfyUI node pack).  This module is the single
source of truth for the rig: the HTTP API serves the table, the UI and the
WebMCP tool schemas read it from there, and the node pack ships an identical
copy.  Compilation is pure and deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

_VOCAB_PATH = Path(__file__).with_name("camera_vocab.json")

_CACHE: Optional[Dict[str, Any]] = None


class CameraError(ValueError):
    """Raised when a rig names a move/lens/speed/framing that does not exist."""


def load_vocab() -> Dict[str, Any]:
    """Return the vocabulary table (parsed once, then cached)."""
    global _CACHE
    if _CACHE is None:
        with _VOCAB_PATH.open("r", encoding="utf-8") as fh:
            _CACHE = json.load(fh)
    return _CACHE


def vocab() -> Dict[str, Any]:
    """Public vocabulary for the API/UI: ids, labels and the clause templates."""
    raw = load_vocab()
    return {
        "version": raw.get("version", 1),
        "moves": [{"id": k, **v} for k, v in raw["moves"].items()],
        "lenses": [{"id": k, **v} for k, v in raw["lenses"].items()],
        "speeds": [{"id": k, **v} for k, v in raw["speeds"].items()],
        "framings": [{"id": k, **v} for k, v in raw["framings"].items()],
    }


def move_ids() -> list:
    return list(load_vocab()["moves"])


def lens_ids() -> list:
    return list(load_vocab()["lenses"])


def speed_ids() -> list:
    return list(load_vocab()["speeds"])


def framing_ids() -> list:
    return list(load_vocab()["framings"])


def default_rig() -> Dict[str, str]:
    return {"move": "static", "lens": "50mm", "speed": "slow", "framing": "medium"}


def validate_rig(rig: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """Validate (and fill defaults for) a rig.  Raises CameraError on a bad value."""
    out = default_rig()
    if rig is None:
        return out
    if not isinstance(rig, Mapping):
        raise CameraError("camera must be an object with move/lens/speed/framing")
    for key, allowed in (
        ("move", move_ids()),
        ("lens", lens_ids()),
        ("speed", speed_ids()),
        ("framing", framing_ids()),
    ):
        value = rig.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or value not in allowed:
            raise CameraError(
                "unknown camera %s %r (choose one of: %s)" % (key, value, ", ".join(allowed))
            )
        out[key] = value
    return out


def _clause_for(section: str, ident: str, speed_adverb: str) -> str:
    entry = load_vocab()[section][ident]
    template = entry["clause"]
    if "{speed}" in template:
        return template.replace("{speed}", speed_adverb)
    return template


def compile(rig: Optional[Mapping[str, Any]], prompt: str) -> str:
    """Compile a rig + subject prompt into Wan-friendly text.

    Shape: subject prompt first, then explicit camera clauses, then the lens
    look, then the framing.  Deterministic: same inputs -> identical bytes.
    """
    r = validate_rig(rig)
    parts = []
    subject = (prompt or "").strip()
    if subject:
        parts.append(subject if subject[-1] in ".!?" else subject + ".")
    adverb = load_vocab()["speeds"][r["speed"]]["adverb"]
    parts.append(_clause_for("moves", r["move"], adverb) + ".")
    parts.append(_clause_for("lenses", r["lens"], adverb) + ".")
    parts.append(_clause_for("framings", r["framing"], adverb) + ".")
    return " ".join(parts)


def rig_summary(rig: Optional[Mapping[str, Any]]) -> str:
    """Short human label, e.g. 'dolly in / 35mm / slow / close up'."""
    r = validate_rig(rig)
    v = load_vocab()
    return " / ".join(
        [
            v["moves"][r["move"]]["label"].lower(),
            v["lenses"][r["lens"]]["label"].lower(),
            v["speeds"][r["speed"]]["label"].lower(),
            v["framings"][r["framing"]]["label"].lower(),
        ]
    )


DEFAULT_NEGATIVE = (
    "blurry, low quality, distorted, watermark, text, still frame, "
    "duplicate subject, extra limbs"
)


def iter_move_ids() -> Iterable[str]:
    return iter(load_vocab()["moves"])
