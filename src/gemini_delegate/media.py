"""Media handling: inline base64 vs the Files API, with a content-hash cache.

Policy (CLAUDE.md §6):
- single-shot + small image + no session  -> inline base64 part
- all video, large images, ANY media in a --session -> upload via the Files API
  and reference by URI (keeps multi-turn cheap and session files small)
- a ``video`` URL (e.g. a YouTube link) is passed through as file-data, no upload
- uploads are cached by ``sha256(file_bytes)`` so the same file isn't sent twice
- Files API objects expire (~48h); an expired cache hit warns and re-uploads,
  and explicit cleanup is available via ``cleanup_uploads``
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.genai import types

_OCTET_STREAM = "application/octet-stream"


class MediaError(Exception):
    """Raised on an unreadable or unusable media input."""


def is_url(src: str) -> bool:
    return src.startswith(("http://", "https://"))


def guess_mime(path_or_url: str) -> str | None:
    return mimetypes.guess_type(path_or_url)[0]


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prepare_image_part(
    client: Any,
    path: str | Path,
    *,
    inline_max_bytes: int,
    force_upload: bool,
    cache: dict[str, dict[str, Any]],
    now: datetime | None = None,
    warnings: list[str] | None = None,
) -> types.Part:
    """Build a request part for an image, inlining small single-shot images."""
    if force_upload or os.path.getsize(path) > inline_max_bytes:
        entry = upload_cached(client, path, cache=cache, now=now, warnings=warnings)
        return types.Part.from_uri(file_uri=entry["uri"], mime_type=entry["mime"])
    data = Path(path).read_bytes()
    return types.Part.from_bytes(data=data, mime_type=guess_mime(str(path)) or _OCTET_STREAM)


def prepare_video_part(
    client: Any,
    src: str,
    *,
    cache: dict[str, dict[str, Any]],
    now: datetime | None = None,
    warnings: list[str] | None = None,
) -> types.Part:
    """Build a request part for a video: URL pass-through, else uploaded."""
    if is_url(src):
        # The SDK requires a mime type and can't guess one from an extension-less
        # URL (e.g. a YouTube link), so fall back to a concrete video type.
        return types.Part.from_uri(file_uri=src, mime_type=guess_mime(src) or "video/mp4")
    entry = upload_cached(client, src, cache=cache, now=now, warnings=warnings)
    return types.Part.from_uri(file_uri=entry["uri"], mime_type=entry["mime"])


def upload_cached(
    client: Any,
    path: str | Path,
    *,
    cache: dict[str, dict[str, Any]],
    now: datetime | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Upload a file via the Files API, reusing a fresh cached entry if present."""
    digest = file_sha256(path)
    entry = cache.get(digest)
    if entry is not None and not _is_expired(entry, now):
        return entry
    if entry is not None and warnings is not None:
        warnings.append(f"Files API upload for {path} looks expired; re-uploading.")
    uploaded = client.files.upload(file=str(path))
    entry = {
        "uri": uploaded.uri,
        "mime": uploaded.mime_type,
        "name": uploaded.name,
        "expires": _iso_or_none(uploaded.expiration_time),
    }
    cache[digest] = entry
    return entry


def cleanup_uploads(client: Any, cache: dict[str, dict[str, Any]]) -> list[str]:
    """Delete every uploaded file recorded in the cache; return deleted names."""
    deleted: list[str] = []
    for entry in cache.values():
        name = entry.get("name")
        if name:
            client.files.delete(name=name)
            deleted.append(name)
    return deleted


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _is_expired(entry: dict[str, Any], now: datetime | None) -> bool:
    expires = entry.get("expires")
    if not expires:
        return False
    moment = now or datetime.now(timezone.utc)
    return datetime.fromisoformat(expires) <= moment
