"""Media store: uploaded references and rendered outputs.

Files live under ``$CINEMA_DATA_DIR/media``; metadata is a single JSON map so a
restart still lists everything.  A media id is opaque (``m_<12 hex>``); the
extension lives in the metadata, so a client never guesses a path.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".gif", ".m4v"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def kind_for(ext: str) -> str:
    ext = (ext or "").lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    return "file"


def new_id(prefix: str) -> str:
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


class MediaStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.dir = self.data_dir / "media"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.data_dir / "media.json"
        self._lock = threading.RLock()
        self._items: Dict[str, Dict[str, Any]] = self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self.meta_path.exists():
            return {}
        try:
            with self.meta_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        tmp = self.meta_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._items, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.meta_path)

    # -- writes -----------------------------------------------------------
    def add_bytes(
        self, data: bytes, ext: str, origin: str = "upload", name: Optional[str] = None
    ) -> Dict[str, Any]:
        ext = (ext or ".bin").lower()
        if not ext.startswith("."):
            ext = "." + ext
        media_id = new_id("m")
        target = self.dir / (media_id + ext)
        target.write_bytes(data)
        record = {
            "media_id": media_id,
            "kind": kind_for(ext),
            "ext": ext,
            "filename": name or target.name,
            "bytes": len(data),
            "origin": origin,
            "created": now_iso(),
        }
        with self._lock:
            self._items[media_id] = record
            self._save()
        return dict(record)

    def add_file(self, src: Path, origin: str = "render", ext: Optional[str] = None) -> Dict[str, Any]:
        src = Path(src)
        suffix = (ext or src.suffix or ".bin").lower()
        media_id = new_id("m")
        if not suffix.startswith("."):
            suffix = "." + suffix
        target = self.dir / (media_id + suffix)
        shutil.copyfile(src, target)
        record = {
            "media_id": media_id,
            "kind": kind_for(suffix),
            "ext": suffix,
            "filename": target.name,
            "bytes": target.stat().st_size,
            "origin": origin,
            "created": now_iso(),
        }
        with self._lock:
            self._items[media_id] = record
            self._save()
        return dict(record)

    # -- reads ------------------------------------------------------------
    def get(self, media_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            rec = self._items.get(media_id)
            return dict(rec) if rec else None

    def path(self, media_id: str) -> Optional[Path]:
        rec = self.get(media_id)
        if not rec:
            return None
        p = self.dir / (media_id + rec["ext"])
        return p if p.exists() else None

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = [dict(v) for v in self._items.values()]
        items.sort(key=lambda r: r.get("created", ""), reverse=True)
        return items

    def url(self, media_id: str) -> str:
        return "/api/media/%s" % media_id
