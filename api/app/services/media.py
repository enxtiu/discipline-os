"""Meal thumbnails on disk (``MEDIA_DIR``), and JPEG sanity checks.

Privacy contract (stage 3):

* the analysed **original photo is never written to disk** — it lives in
  memory for the duration of the AI call only;
* a **400 px thumbnail** is kept only when the user enabled
  "keep photo thumbnail": it waits in ``pending/<job>.jpg`` until the meal is
  saved (then moves to ``meals/<user>/<meal>.jpg``) and is deleted otherwise
  — on save without the setting, on meal deletion, or by the nightly
  ``ai_housekeeping`` job after ``AI_PENDING_THUMB_TTL_HOURS``.

Paths are always built from ids, never from client input.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import Unprocessable
from app.core.logging import get_logger

log = get_logger(__name__)

THUMB_MAX_PX = 480  # the client sends 400 px; a little slack for rounding
THUMB_MAX_BYTES = 250_000

_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


@dataclass(frozen=True, slots=True)
class JpegInfo:
    width: int
    height: int
    size: int


def jpeg_info(data: bytes) -> JpegInfo | None:
    """Width/height from the SOF segment; ``None`` if this is not a readable JPEG.

    Enough to refuse non-images and oversized uploads without an imaging
    library (the stack pins no Pillow).
    """
    if len(data) < 4 or data[:3] != b"\xff\xd8\xff":
        return None
    index = 2
    length = len(data)
    while index + 4 <= length:
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker == 0xFF:  # fill byte
            index += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # SOI / TEM / RSTn have no length
            index += 2
            continue
        segment = int.from_bytes(data[index + 2 : index + 4], "big")
        if segment < 2:
            return None
        if marker in _SOF_MARKERS and index + 9 <= length:
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return JpegInfo(width=width, height=height, size=length) if width and height else None
        if marker == 0xDA:  # start of scan before any SOF: broken file
            return None
        index += 2 + segment
    return None


def check_photo(data: bytes) -> JpegInfo:
    settings = get_settings()
    if len(data) > settings.ai_max_image_bytes:
        raise Unprocessable(
            "The photo is too large", code="image_too_large", details={"max_bytes": settings.ai_max_image_bytes}
        )
    info = jpeg_info(data)
    if info is None:
        raise Unprocessable("The photo must be a JPEG image", code="image_invalid")
    if max(info.width, info.height) > settings.ai_max_image_px:
        raise Unprocessable(
            "The photo resolution is too high", code="image_too_large", details={"max_px": settings.ai_max_image_px}
        )
    return info


def check_thumbnail(data: bytes) -> JpegInfo | None:
    """A bad thumbnail never fails the analysis — it is simply not kept."""
    info = jpeg_info(data)
    if info is None or len(data) > THUMB_MAX_BYTES or max(info.width, info.height) > THUMB_MAX_PX:
        return None
    return info


def _root() -> Path:
    return Path(get_settings().media_dir)


def _resolve(relative: str) -> Path:
    root = _root().resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError(f"media path escapes MEDIA_DIR: {relative}")
    return path


def pending_thumb_relpath(job_id: uuid.UUID) -> str:
    return f"pending/{job_id}.jpg"


def meal_thumb_relpath(user_id: uuid.UUID, meal_id: uuid.UUID) -> str:
    return f"meals/{user_id}/{meal_id}.jpg"


def write(relative: str, data: bytes) -> None:
    path = _resolve(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def move(source: str, target: str) -> bool:
    src, dst = _resolve(source), _resolve(target)
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    return True


def delete(relative: str | None) -> bool:
    if not relative:
        return False
    path = _resolve(relative)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def path_of(relative: str | None) -> Path | None:
    if not relative:
        return None
    path = _resolve(relative)
    return path if path.exists() else None


def prune_pending(older_than_seconds: float) -> int:
    """Delete pending thumbnails nobody saved a meal for."""
    folder = _root() / "pending"
    if not folder.exists():
        return 0
    cutoff = time.time() - older_than_seconds
    removed = 0
    for entry in folder.glob("*.jpg"):
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed
