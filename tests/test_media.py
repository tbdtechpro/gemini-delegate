"""Offline tests for media handling (CLAUDE.md §6, §10).

The Gemini client is faked: `files.upload` returns a stand-in File and is
asserted on (call counts, args). No network.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gemini_delegate import media


def _fake_file(uri="files/remote", mime="image/png", name="files/remote", expires=None):
    return SimpleNamespace(
        uri=uri, mime_type=mime, name=name, expiration_time=expires, state="ACTIVE"
    )


def _fake_client(upload_return=None):
    client = MagicMock()
    client.files.upload.return_value = upload_return or _fake_file()
    return client


def _img(tmp_path, name="i.png", size=64):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG" + b"\x00" * (size - 4))
    return p


# --- inline vs Files API decision ----------------------------------------------


def test_small_single_shot_image_goes_inline(tmp_path):
    client = _fake_client()
    part = media.prepare_image_part(
        client, _img(tmp_path), inline_max_bytes=4096, force_upload=False, cache={}
    )
    assert part.inline_data is not None
    assert part.file_data is None
    client.files.upload.assert_not_called()


def test_large_image_is_uploaded(tmp_path):
    client = _fake_client()
    part = media.prepare_image_part(
        client, _img(tmp_path, size=8192), inline_max_bytes=4096, force_upload=False, cache={}
    )
    assert part.file_data is not None
    assert part.file_data.file_uri == "files/remote"
    client.files.upload.assert_called_once()


def test_force_upload_sends_small_image_via_files_api(tmp_path):
    # Any media in a --session must go through the Files API even when small.
    client = _fake_client()
    part = media.prepare_image_part(
        client, _img(tmp_path), inline_max_bytes=4096, force_upload=True, cache={}
    )
    assert part.file_data is not None
    client.files.upload.assert_called_once()


# --- video --------------------------------------------------------------------


def test_local_video_is_uploaded(tmp_path):
    client = _fake_client(_fake_file(uri="files/vid", mime="video/mp4", name="files/vid"))
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"\x00" * 128)
    part = media.prepare_video_part(client, str(vid), cache={})
    assert part.file_data.file_uri == "files/vid"
    client.files.upload.assert_called_once()


def test_video_url_passes_through_without_upload():
    client = _fake_client()
    part = media.prepare_video_part(client, "https://youtu.be/dQw4w9WgXcQ", cache={})
    assert part.file_data.file_uri == "https://youtu.be/dQw4w9WgXcQ"
    client.files.upload.assert_not_called()


# --- cache --------------------------------------------------------------------


def test_same_file_uploaded_only_once(tmp_path):
    client = _fake_client()
    cache: dict = {}
    img = _img(tmp_path, size=8192)
    media.prepare_image_part(client, img, inline_max_bytes=4096, force_upload=False, cache=cache)
    media.prepare_image_part(client, img, inline_max_bytes=4096, force_upload=False, cache=cache)
    client.files.upload.assert_called_once()
    assert len(cache) == 1


def test_expired_cache_entry_warns_and_reuploads(tmp_path):
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    client = _fake_client(_fake_file(expires=past))
    cache: dict = {}
    warnings: list = []
    img = _img(tmp_path, size=8192)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    media.prepare_image_part(
        client, img, inline_max_bytes=4096, force_upload=False, cache=cache, now=now, warnings=warnings
    )
    # Re-uploads with the same client whose returned file is already expired.
    media.prepare_image_part(
        client, img, inline_max_bytes=4096, force_upload=False, cache=cache, now=now, warnings=warnings
    )
    assert client.files.upload.call_count == 2
    assert any("expired" in w.lower() for w in warnings)


def test_fresh_cache_entry_is_reused_without_warning(tmp_path):
    future = datetime.now(timezone.utc) + timedelta(hours=24)
    client = _fake_client(_fake_file(expires=future))
    cache: dict = {}
    warnings: list = []
    img = _img(tmp_path, size=8192)
    media.prepare_image_part(client, img, inline_max_bytes=4096, force_upload=False, cache=cache, warnings=warnings)
    media.prepare_image_part(client, img, inline_max_bytes=4096, force_upload=False, cache=cache, warnings=warnings)
    client.files.upload.assert_called_once()
    assert warnings == []


# --- cleanup ------------------------------------------------------------------


def test_cleanup_deletes_each_uploaded_file():
    client = _fake_client()
    cache = {
        "h1": {"uri": "files/a", "mime": "image/png", "name": "files/a", "expires": None},
        "h2": {"uri": "files/b", "mime": "image/png", "name": "files/b", "expires": None},
    }
    deleted = media.cleanup_uploads(client, cache)
    assert set(deleted) == {"files/a", "files/b"}
    assert client.files.delete.call_count == 2
