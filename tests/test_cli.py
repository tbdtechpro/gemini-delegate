"""Per-command CLI behavior (CLAUDE.md §4, §5, §10)."""
import json

from click.testing import CliRunner

from gemini_delegate.cli import cli
from _helpers import image_response, text_response, write_png


def test_describe_maps_to_envelope(fake_gemini):
    fake_gemini.models.generate_content.return_value = text_response("a red square")
    runner = CliRunner()
    with runner.isolated_filesystem():
        write_png("i.png")
        res = runner.invoke(cli, ["describe", "i.png", "--prompt", "what is this?"])
    assert res.exit_code == 0
    env = json.loads(res.output)
    assert env["op"] == "describe"
    assert env["text"] == "a red square"


def test_describe_requires_an_image(fake_gemini):
    res = CliRunner().invoke(cli, ["describe", "--prompt", "x"])
    assert res.exit_code == 2  # no image path


def test_ask_json_mode_populates_json_field(fake_gemini):
    fake_gemini.models.generate_content.return_value = text_response('{"answer": 42}')
    res = CliRunner().invoke(cli, ["ask", "--prompt", "q", "--json"])
    assert res.exit_code == 0
    env = json.loads(res.output)
    assert env["json"] == {"answer": 42}


def test_ask_schema_implies_json(fake_gemini):
    fake_gemini.models.generate_content.return_value = text_response('{"x": "y"}')
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("schema.json", "w") as fh:
            json.dump({"type": "object", "properties": {"x": {"type": "string"}}}, fh)
        res = runner.invoke(cli, ["ask", "--prompt", "q", "--schema", "schema.json"])
    assert res.exit_code == 0
    env = json.loads(res.output)
    assert env["json"] == {"x": "y"}


def test_image_writes_file_and_lists_it(fake_gemini):
    fake_gemini.models.generate_content.return_value = image_response(1)
    runner = CliRunner()
    with runner.isolated_filesystem():
        res = runner.invoke(cli, ["image", "--prompt", "draw a cat", "--out", "out.png"])
        assert res.exit_code == 0
        env = json.loads(res.output)
        assert env["op"] == "image"
        assert len(env["files"]) == 1
        assert env["files"][0].endswith("out.png")
        import os

        assert os.path.isfile("out.png")


def test_image_rejects_bad_n(fake_gemini):
    res = CliRunner().invoke(cli, ["image", "--prompt", "x", "--out", "o.png", "--n", "0"])
    assert res.exit_code == 2


def test_video_url_runs(fake_gemini):
    fake_gemini.models.generate_content.return_value = text_response("a summary")
    res = CliRunner().invoke(cli, ["video", "https://youtu.be/x", "--prompt", "summarize"])
    assert res.exit_code == 0
    env = json.loads(res.output)
    assert env["op"] == "video"


def test_video_missing_local_source_is_usage_error(fake_gemini):
    res = CliRunner().invoke(cli, ["video", "/no/such/clip.mp4", "--prompt", "x"])
    assert res.exit_code == 2


def test_session_file_is_written(fake_gemini):
    fake_gemini.models.generate_content.return_value = text_response("first")
    runner = CliRunner()
    with runner.isolated_filesystem():
        res = runner.invoke(cli, ["ask", "--prompt", "q1", "--session", "s.json"])
        assert res.exit_code == 0
        env = json.loads(res.output)
        assert env["session"].endswith("s.json")
        assert json.loads(open("s.json").read())["role"] == "text"


def test_debug_flag_does_not_corrupt_stdout(fake_gemini):
    # On failure with --debug, the traceback goes to stderr; stdout stays clean JSON.
    fake_gemini.models.generate_content.side_effect = RuntimeError("boom")
    res = CliRunner().invoke(cli, ["ask", "--prompt", "q", "--debug"])
    assert res.exit_code == 1
    env = json.loads(res.stdout)  # stdout is exactly one clean envelope...
    assert env["ok"] is False
    assert env["error"]["type"] == "internal"
    assert "Traceback" in res.stderr  # ...and the traceback went to stderr


def test_prompt_file_supplies_the_prompt(fake_gemini):
    # --prompt-file lets long/multi-line prompts avoid shell quoting + approval friction.
    fake_gemini.models.generate_content.return_value = text_response("ok")
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("p.txt", "w") as fh:
            fh.write("a multi\nline prompt")
        res = runner.invoke(cli, ["ask", "--prompt-file", "p.txt"])
    assert res.exit_code == 0
    sent = fake_gemini.models.generate_content.call_args.kwargs["contents"]
    assert "multi" in sent[0].parts[0].text


def test_prompt_and_prompt_file_are_mutually_exclusive(fake_gemini):
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("p.txt", "w") as fh:
            fh.write("x")
        res = runner.invoke(cli, ["ask", "--prompt", "y", "--prompt-file", "p.txt"])
    assert res.exit_code == 2


def test_neither_prompt_nor_prompt_file_is_usage_error(fake_gemini):
    res = CliRunner().invoke(cli, ["ask"])
    assert res.exit_code == 2


def _interaction_ok(payload=None):
    import base64
    import io as _io
    from PIL import Image as _Image
    from types import SimpleNamespace
    if payload is None:
        buf = _io.BytesIO()
        _Image.new("RGB", (8, 8), (0, 0, 200)).save(buf, format="JPEG")  # API returns JPEG
        payload = buf.getvalue()
    b64 = base64.b64encode(payload).decode()
    return SimpleNamespace(output_image=None,
                           steps=[SimpleNamespace(content=[SimpleNamespace(type="image", data=b64)])],
                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))


def test_image_size_aspect_endpoint_options(fake_gemini):
    fake_gemini.interactions.create.return_value = _interaction_ok()
    runner = CliRunner()
    with runner.isolated_filesystem():
        res = runner.invoke(cli, ["image", "--prompt", "x", "--out", "o.png",
                                  "--size", "4K", "--aspect-ratio", "16:9", "--endpoint", "interactions"])
        assert res.exit_code == 0
        kw = fake_gemini.interactions.create.call_args.kwargs
        assert kw["response_format"]["image_size"] == "4K"
        import os
        assert os.path.isfile("o.png")


def test_image_rejects_bad_size(fake_gemini):
    res = CliRunner().invoke(cli, ["image", "--prompt", "x", "--out", "o.png", "--size", "8K"])
    assert res.exit_code == 2  # not in choice 512/1K/2K/4K


def test_image_transparent_flag(fake_gemini):
    import io as _io, base64
    from types import SimpleNamespace
    from PIL import Image
    src = Image.new("RGB", (16, 16), (255, 0, 255))
    for x in range(6, 10):
        for y in range(6, 10):
            src.putpixel((x, y), (0, 200, 0))
    buf = _io.BytesIO(); src.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    fake_gemini.interactions.create.return_value = SimpleNamespace(
        output_image=None, steps=[SimpleNamespace(content=[SimpleNamespace(type="image", data=b64)])],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1))
    runner = CliRunner()
    with runner.isolated_filesystem():
        res = runner.invoke(cli, ["image", "--prompt", "x", "--out", "o.png", "--transparent"])
        assert res.exit_code == 0
        assert Image.open("o.png").mode == "RGBA"


def test_image_transparent_with_jpg_is_usage_error(fake_gemini):
    res = CliRunner().invoke(cli, ["image", "--prompt", "x", "--out", "o.jpg", "--transparent"])
    assert res.exit_code == 2


def test_image_bad_chroma_key_color_is_usage_error(fake_gemini):
    res = CliRunner().invoke(cli, ["image", "--prompt", "x", "--out", "o.png", "--chroma-key", "nope"])
    assert res.exit_code == 2


def test_image_chroma_tolerance_out_of_range_is_usage_error(fake_gemini):
    res = CliRunner().invoke(cli, ["image", "--prompt", "x", "--out", "o.png", "--chroma-tolerance", "999"])
    assert res.exit_code == 2


def test_image_chroma_key_with_jpg_out_is_usage_error(fake_gemini):
    res = CliRunner().invoke(cli, ["image", "--prompt", "x", "--out", "o.jpg", "--chroma-key", "#FF00FF"])
    assert res.exit_code == 2
