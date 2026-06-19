"""Offline tests for the four core operations (CLAUDE.md §6, §10).

The Gemini client is faked end to end. Core returns plain dicts (no envelope,
no exit codes — that's the CLI's job).
"""
import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from gemini_delegate import core
from gemini_delegate.config import load_config
from gemini_delegate.core import CoreError


@pytest.fixture
def cfg():
    return load_config()


def _png(tmp_path, name="i.png", color=(255, 0, 0)):
    p = tmp_path / name
    Image.new("RGB", (8, 8), color).save(p, format="PNG")
    return p


def _text_response(text, prompt_tokens=5, cand_tokens=7):
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens, candidates_token_count=cand_tokens
        ),
    )


def _client_returning(resp):
    client = MagicMock()
    client.models.generate_content.return_value = resp
    return client


# --- describe -----------------------------------------------------------------


def test_describe_returns_text_files_and_usage(cfg, tmp_path):
    client = _client_returning(_text_response("a red square"))
    result = core.describe(client, cfg, images=[str(_png(tmp_path))], prompt="what is this?")
    assert result["op"] == "describe"
    assert result["text"] == "a red square"
    assert result["json"] is None
    assert result["files"] == []
    assert result["session"] is None
    assert result["model"] == cfg.resolve_model("vision")
    assert result["usage"] == {"input_tokens": 5, "output_tokens": 7}
    # model id, not role, reaches the SDK
    assert client.models.generate_content.call_args.kwargs["model"] == cfg.resolve_model("vision")


def test_describe_json_mode_parses_and_sets_mime(cfg, tmp_path):
    client = _client_returning(_text_response('{"label": "cat"}'))
    result = core.describe(
        client, cfg, images=[str(_png(tmp_path))], prompt="classify", want_json=True
    )
    assert result["json"] == {"label": "cat"}
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.response_mime_type == "application/json"


def test_json_parse_failure_raises_with_context(cfg, tmp_path):
    client = _client_returning(_text_response("definitely not json"))
    with pytest.raises(CoreError) as exc:
        core.describe(client, cfg, images=[str(_png(tmp_path))], prompt="x", want_json=True)
    assert exc.value.type == "json_parse"
    # context preserved so the CLI can still fill model/usage in the envelope
    assert exc.value.details["model"] == cfg.resolve_model("vision")
    assert exc.value.details["usage"] == {"input_tokens": 5, "output_tokens": 7}


def test_schema_sets_response_schema(cfg, tmp_path):
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps({"type": "object", "properties": {"x": {"type": "string"}}}))
    client = _client_returning(_text_response('{"x": "y"}'))
    core.describe(client, cfg, images=[str(_png(tmp_path))], prompt="x", schema=str(schema))
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema == {"type": "object", "properties": {"x": {"type": "string"}}}


def test_explicit_model_overrides_role(cfg, tmp_path):
    client = _client_returning(_text_response("ok"))
    core.describe(client, cfg, images=[str(_png(tmp_path))], prompt="x", model="reason")
    assert client.models.generate_content.call_args.kwargs["model"] == cfg.resolve_model("reason")


# --- ask ----------------------------------------------------------------------


def test_ask_is_text_only(cfg):
    client = _client_returning(_text_response("the answer"))
    result = core.ask(client, cfg, prompt="a question")
    assert result["op"] == "ask"
    assert result["text"] == "the answer"
    assert result["model"] == cfg.resolve_model("text")
    contents = client.models.generate_content.call_args.kwargs["contents"]
    assert len(contents) == 1  # single user turn
    assert contents[0].parts[0].text == "a question"


# --- video --------------------------------------------------------------------


def test_video_url_does_not_upload(cfg):
    client = _client_returning(_text_response("a summary"))
    result = core.video(client, cfg, src="https://youtu.be/x", prompt="summarize")
    assert result["model"] == cfg.resolve_model("video")
    client.files.upload.assert_not_called()


def test_video_local_file_uploads(cfg, tmp_path):
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"\x00" * 64)
    client = _client_returning(_text_response("a summary"))
    client.files.upload.return_value = SimpleNamespace(
        uri="files/vid", mime_type="video/mp4", name="files/vid", expiration_time=None
    )
    core.video(client, cfg, src=str(vid), prompt="summarize")
    client.files.upload.assert_called_once()


# --- image --------------------------------------------------------------------


def _image_response(n_images):
    def _save_factory(payload):
        def _save(path):
            with open(path, "wb") as fh:
                fh.write(payload)
        return _save

    parts = []
    for i in range(n_images):
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (0, i * 10, 0)).save(buf, format="PNG")
        payload = buf.getvalue()
        part = SimpleNamespace(
            inline_data=SimpleNamespace(data=payload),
            as_image=lambda p=payload: SimpleNamespace(save=_save_factory(p)),
        )
        parts.append(part)
    return SimpleNamespace(
        text=None,
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))],
        usage_metadata=SimpleNamespace(prompt_token_count=3, candidates_token_count=0),
    )


def test_image_writes_file(cfg, tmp_path):
    out = tmp_path / "out.png"
    client = _client_returning(_image_response(1))
    result = core.image(client, cfg, prompt="draw a cat", out=str(out))
    assert result["op"] == "image"
    assert result["model"] == cfg.resolve_model("image")
    assert result["files"] == [str(out.resolve())]
    assert out.is_file() and out.stat().st_size > 0
    config = client.models.generate_content.call_args.kwargs["config"]
    assert "IMAGE" in [str(m).upper() for m in config.response_modalities]


def test_image_multiple_n_numbers_files(cfg, tmp_path):
    out = tmp_path / "out.png"
    client = _client_returning(_image_response(2))
    result = core.image(client, cfg, prompt="draw", out=str(out), n=2)
    assert len(result["files"]) == 2
    assert (tmp_path / "out.png").is_file()
    assert (tmp_path / "out_2.png").is_file()


def test_image_no_data_raises(cfg, tmp_path):
    empty = SimpleNamespace(
        text=None, candidates=[SimpleNamespace(content=SimpleNamespace(parts=[]))],
        usage_metadata=SimpleNamespace(prompt_token_count=1, candidates_token_count=0),
    )
    client = _client_returning(empty)
    with pytest.raises(CoreError) as exc:
        core.image(client, cfg, prompt="draw", out=str(tmp_path / "o.png"))
    assert exc.value.type == "no_image"


# --- sessions -----------------------------------------------------------------


def test_session_round_trip_grows_and_replays(cfg, tmp_path):
    path = tmp_path / "s.json"
    client = _client_returning(_text_response("first"))
    core.ask(client, cfg, prompt="q1", session_path=str(path))
    # After one exchange: user + model = 2 turns persisted.
    assert len(json.loads(path.read_text())["contents"]) == 2

    client.models.generate_content.return_value = _text_response("second")
    core.ask(client, cfg, prompt="q2", session_path=str(path))
    # Second call replays prior 2 turns + the new user turn = 3 contents sent.
    sent = client.models.generate_content.call_args.kwargs["contents"]
    assert len(sent) == 3
    assert len(json.loads(path.read_text())["contents"]) == 4


def test_describe_with_session_forces_upload(cfg, tmp_path):
    path = tmp_path / "s.json"
    client = _client_returning(_text_response("desc"))
    client.files.upload.return_value = SimpleNamespace(
        uri="files/img", mime_type="image/png", name="files/img", expiration_time=None
    )
    # Small image that would normally go inline, but a session forces the Files API.
    core.describe(client, cfg, images=[str(_png(tmp_path))], prompt="x", session_path=str(path))
    client.files.upload.assert_called_once()
