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


def _gc_image_response(n=1):
    import io
    from PIL import Image
    cands = []
    for i in range(n):
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (0, i * 10, 0)).save(buf, format="PNG")
        part = SimpleNamespace(inline_data=SimpleNamespace(data=buf.getvalue()))
        cands.append(SimpleNamespace(content=SimpleNamespace(parts=[part])))
    return SimpleNamespace(candidates=cands,
                           usage_metadata=SimpleNamespace(prompt_token_count=3, candidates_token_count=7))


def _interaction_response(payload=None):
    import base64
    import io as _io
    from PIL import Image as _Image
    if payload is None:
        buf = _io.BytesIO()
        _Image.new("RGB", (8, 8), (0, 0, 200)).save(buf, format="JPEG")  # API returns JPEG
        payload = buf.getvalue()
    b64 = base64.b64encode(payload).decode()
    block = SimpleNamespace(type="image", data=b64)
    return SimpleNamespace(output_image=None, steps=[SimpleNamespace(content=[block])],
                           usage=SimpleNamespace(input_tokens=2, output_tokens=5))


def test_image_default_endpoint_uses_interactions(cfg, tmp_path):
    out = tmp_path / "out.png"
    client = MagicMock()
    client.interactions.create.return_value = _interaction_response()
    result = core.image(client, cfg, prompt="draw a cat", out=str(out))
    assert result["op"] == "image"
    assert result["files"] == [str(out.resolve())]
    assert out.is_file()
    client.models.generate_content.assert_not_called()  # interactions handled it
    assert any("interactions" in w for w in result["warnings"])


def test_image_endpoint_generate_content(cfg, tmp_path):
    out = tmp_path / "out.png"
    client = MagicMock()
    client.models.generate_content.return_value = _gc_image_response(1)
    result = core.image(client, cfg, prompt="draw", out=str(out), endpoint="generate_content")
    assert out.is_file()
    client.interactions.create.assert_not_called()


def test_image_auto_falls_back_to_generate_content(cfg, tmp_path):
    out = tmp_path / "out.png"
    client = MagicMock()
    client.interactions.create.side_effect = RuntimeError("beta down")
    client.models.generate_content.return_value = _gc_image_response(1)
    result = core.image(client, cfg, prompt="draw", out=str(out))  # default policy = auto
    assert out.is_file()
    assert any("fell back" in w for w in result["warnings"])


def test_image_interactions_only_failure_raises(cfg, tmp_path):
    client = MagicMock()
    client.interactions.create.side_effect = RuntimeError("boom")
    with pytest.raises(core.CoreError):
        core.image(client, cfg, prompt="x", out=str(tmp_path / "o.png"), endpoint="interactions")


def test_image_size_and_aspect_passed_to_interactions(cfg, tmp_path):
    client = MagicMock()
    client.interactions.create.return_value = _interaction_response()
    core.image(client, cfg, prompt="x", out=str(tmp_path / "o.png"), size="4K", aspect_ratio="16:9", model="image_pro")
    kw = client.interactions.create.call_args.kwargs
    assert kw["model"] == cfg.resolve_model("image_pro")  # gemini-3-pro-image
    assert kw["response_format"]["image_size"] == "4K"


def test_image_no_data_raises(cfg, tmp_path):
    client = MagicMock()
    client.interactions.create.return_value = SimpleNamespace(output_image=None, steps=[],
                                                              usage=SimpleNamespace(input_tokens=0, output_tokens=0))
    client.models.generate_content.return_value = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[]))],
        usage_metadata=SimpleNamespace(prompt_token_count=0, candidates_token_count=0))
    with pytest.raises(core.CoreError) as exc:
        core.image(client, cfg, prompt="x", out=str(tmp_path / "o.png"))
    assert exc.value.type == "no_image"


def test_image_transcodes_jpeg_to_png_out(cfg, tmp_path):
    from PIL import Image
    out = tmp_path / "out.png"  # ask for PNG; the backend returns JPEG bytes
    client = MagicMock()
    client.interactions.create.return_value = _interaction_response()  # JPEG payload
    result = core.image(client, cfg, prompt="draw", out=str(out))
    assert result["files"] == [str(out.resolve())]
    assert Image.open(out).format == "PNG"  # real PNG, not JPEG-bytes-in-a-.png


# --- image: transparency / chroma-key ----------------------------------------


def test_image_transparent_keys_and_injects_directive(cfg, tmp_path):
    from PIL import Image
    import io as _io, base64
    # backend returns a magenta field with a small green square (keyable)
    src = Image.new("RGB", (16, 16), (255, 0, 255))
    for x in range(6, 10):
        for y in range(6, 10):
            src.putpixel((x, y), (0, 200, 0))
    buf = _io.BytesIO(); src.save(buf, format="PNG")
    client = MagicMock()
    client.interactions.create.return_value = _interaction_response(payload=buf.getvalue())
    out = tmp_path / "logo.png"
    core.image(client, cfg, prompt="a creature", out=str(out), transparent=True)
    assert Image.open(out).mode == "RGBA"
    assert Image.open(out).getpixel((0, 0))[3] == 0  # bg keyed out
    # directive was appended to the prompt the backend received
    sent = client.interactions.create.call_args.kwargs["input"][0]["text"]
    assert "magenta" in sent.lower() and "a creature" in sent


def test_image_transparent_questionable_keying_warns(cfg, tmp_path):
    from PIL import Image
    import io as _io
    buf = _io.BytesIO(); Image.new("RGB", (16, 16), (0, 0, 200)).save(buf, format="PNG")  # no magenta
    client = MagicMock()
    client.interactions.create.return_value = _interaction_response(payload=buf.getvalue())
    result = core.image(client, cfg, prompt="x", out=str(tmp_path / "o.png"), transparent=True)
    assert any("key" in w for w in result["warnings"])  # ~0% removed -> warning


def test_image_keep_original_writes_both(cfg, tmp_path):
    from PIL import Image
    import io as _io
    buf = _io.BytesIO(); Image.new("RGB", (16, 16), (255, 0, 255)).save(buf, format="JPEG")
    client = MagicMock()
    client.interactions.create.return_value = _interaction_response(payload=buf.getvalue())
    out = tmp_path / "o.png"
    core.image(client, cfg, prompt="x", out=str(out), transparent=True, keep_original=True)
    assert out.is_file() and (tmp_path / "o.orig.jpg").is_file()


def test_image_chroma_key_color_no_directive(cfg, tmp_path):
    from PIL import Image
    import io as _io
    buf = _io.BytesIO(); Image.new("RGB", (16, 16), (0, 255, 0)).save(buf, format="PNG")
    client = MagicMock()
    client.interactions.create.return_value = _interaction_response(payload=buf.getvalue())
    core.image(client, cfg, prompt="just this", out=str(tmp_path / "o.png"), chroma_key="#00FF00")
    sent = client.interactions.create.call_args.kwargs["input"][0]["text"]
    assert sent == "just this"  # primitive does NOT inject a directive


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
