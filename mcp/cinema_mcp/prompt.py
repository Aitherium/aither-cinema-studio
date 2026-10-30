"""Camera rig validation and prompt compilation.

The backend compiles the authoritative prompt when a shot is submitted
(``server/cinema/camera.py``), and it also records that compiled text on the
shot.  This module exists so ``plan_shot`` can show an agent the exact text
BEFORE spending a render, built from the vocabulary the backend serves — so a
renamed move can never silently drift between the two.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .client import CinemaError

DEFAULT_RIG: Dict[str, str] = {
    "move": "static",
    "lens": "50mm",
    "speed": "slow",
    "framing": "medium",
}

SECTIONS = ("moves", "lenses", "speeds", "framings")
_RIG_KEYS = (("move", "moves"), ("lens", "lenses"), ("speed", "speeds"), ("framing", "framings"))


def index_vocab(vocab: Mapping[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """``{"moves": {id: entry}, ...}`` from the API's list-of-dicts shape."""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for section in SECTIONS:
        entries = vocab.get(section) or []
        out[section] = {
            str(e["id"]): e for e in entries if isinstance(e, Mapping) and e.get("id")
        }
    return out


def _ids(index: Dict[str, Dict[str, Dict[str, Any]]], section: str) -> list:
    return list(index.get(section, {}))


def validate_rig(
    rig: Optional[Mapping[str, Any]],
    vocab: Mapping[str, Any],
) -> Dict[str, str]:
    """Fill defaults and reject unknown camera ids, naming the valid ones."""
    index = index_vocab(vocab)
    out = dict(DEFAULT_RIG)
    if rig is None:
        return out
    if not isinstance(rig, Mapping):
        raise CinemaError("camera_rig must be an object with move/lens/speed/framing")
    for key, section in _RIG_KEYS:
        value = rig.get(key)
        if value is None or value == "":
            continue
        allowed = _ids(index, section)
        if not isinstance(value, str):
            raise CinemaError("camera_rig.%s must be a string id" % key)
        if value not in allowed:
            raise CinemaError(
                "unknown camera_rig.%s %r — valid ids: %s"
                % (key, value, ", ".join(allowed))
            )
        out[key] = value
    return out


def _clause(entry: Mapping[str, Any], adverb: str) -> str:
    template = str(entry.get("clause") or "")
    return template.replace("{speed}", adverb) if "{speed}" in template else template


def compile_prompt(
    rig: Mapping[str, Any],
    prompt: str,
    vocab: Mapping[str, Any],
) -> str:
    """Subject first, then move, lens and framing clauses. Deterministic.

    Same shape as the backend's ``camera.compile``: identical bytes for
    identical inputs, which is why ``plan_shot`` is safe to show before a render.
    """
    index = index_vocab(vocab)
    r = validate_rig(rig, vocab)
    parts = []
    subject = (prompt or "").strip()
    if subject:
        parts.append(subject if subject[-1] in ".!?" else subject + ".")
    adverb = str(index["speeds"][r["speed"]].get("adverb") or "")
    parts.append(_clause(index["moves"][r["move"]], adverb) + ".")
    parts.append(_clause(index["lenses"][r["lens"]], adverb) + ".")
    parts.append(_clause(index["framings"][r["framing"]], adverb) + ".")
    return " ".join(parts)


def rig_summary(rig: Mapping[str, Any], vocab: Mapping[str, Any]) -> str:
    """Human label, e.g. ``dolly in / 35mm / slow / close up``."""
    index = index_vocab(vocab)
    r = validate_rig(rig, vocab)
    labels = []
    for key, section in _RIG_KEYS:
        entry = index.get(section, {}).get(r[key]) or {}
        labels.append(str(entry.get("label") or r[key]))
    return " / ".join(labels)
