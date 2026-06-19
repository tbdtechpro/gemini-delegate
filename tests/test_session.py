"""Offline tests for session read/append/write (CLAUDE.md §6, §10).

A session is pure JSON: model role + the running `contents` list + the upload
cache. No SDK, no network — `contents` holds already-serialized part dicts.
"""
import json
from pathlib import Path

import pytest

from gemini_delegate.session import Session, SessionError


def test_new_session_starts_empty():
    s = Session.new("vision")
    assert s.role == "vision"
    assert s.contents == []
    assert s.uploads == {}


def test_append_turns_use_user_and_model_roles():
    s = Session.new("text")
    s.append_user([{"text": "hello"}])
    s.append_model([{"text": "hi there"}])
    assert [c["role"] for c in s.contents] == ["user", "model"]
    assert s.contents[0]["parts"] == [{"text": "hello"}]


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "s.json"
    s = Session.new("vision")
    s.append_user([{"text": "describe"}, {"file_data": {"file_uri": "files/x", "mime_type": "image/png"}}])
    s.append_model([{"text": "a cat"}])
    s.uploads["abc123"] = {"uri": "files/x", "mime": "image/png", "name": "files/x", "expires": None}
    s.save(path)

    loaded = Session.load(path)
    assert loaded.role == "vision"
    assert loaded.contents == s.contents
    assert loaded.uploads == s.uploads


def test_saved_file_is_valid_json(tmp_path):
    path = tmp_path / "s.json"
    s = Session.new("text")
    s.append_user([{"text": "x"}])
    s.save(path)
    data = json.loads(path.read_text())
    assert data["role"] == "text"
    assert data["contents"][0]["role"] == "user"


def test_load_or_create_returns_new_when_missing(tmp_path):
    s = Session.load_or_create(tmp_path / "absent.json", "video")
    assert s.role == "video"
    assert s.contents == []


def test_load_or_create_returns_existing(tmp_path):
    path = tmp_path / "s.json"
    Session.new("vision").save(path)
    s = Session.load_or_create(path, "text")  # role arg ignored when file exists
    assert s.role == "vision"


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(SessionError):
        Session.load(tmp_path / "nope.json")


def test_load_malformed_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    Path(path).write_text("{ not valid json ")
    with pytest.raises(SessionError):
        Session.load(path)


def test_replay_preserves_order_across_many_turns(tmp_path):
    path = tmp_path / "s.json"
    s = Session.new("text")
    for i in range(3):
        s.append_user([{"text": f"q{i}"}])
        s.append_model([{"text": f"a{i}"}])
    s.save(path)
    loaded = Session.load(path)
    assert [p["parts"][0]["text"] for p in loaded.contents] == ["q0", "a0", "q1", "a1", "q2", "a2"]
