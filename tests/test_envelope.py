"""Envelope-shape and exit-code tests (CLAUDE.md §5, §10).

The subagent parses the envelope and nothing else, so its shape and the exit
codes are load-bearing.
"""
import json

import pytest
from click.testing import CliRunner

from gemini_delegate.cli import cli
from _helpers import text_response

_ENVELOPE_KEYS = {
    "ok", "op", "model", "text", "json", "files", "session", "usage", "warnings", "error",
}


def _parse(output):
    return json.loads(output)


def test_success_envelope_has_exact_shape(fake_gemini):
    fake_gemini.models.generate_content.return_value = text_response("hello")
    res = CliRunner().invoke(cli, ["ask", "--prompt", "hi"])
    assert res.exit_code == 0
    env = _parse(res.output)
    assert set(env) == _ENVELOPE_KEYS
    assert env["ok"] is True
    assert env["op"] == "ask"
    assert env["error"] is None
    assert env["text"] == "hello"
    assert env["usage"] == {"input_tokens": 5, "output_tokens": 7}
    assert isinstance(env["files"], list)
    assert isinstance(env["warnings"], list)


def test_failure_envelope_on_missing_key(monkeypatch):
    # Real make_client runs and fails fast — clean envelope, not a traceback.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    res = CliRunner().invoke(cli, ["ask", "--prompt", "hi"])
    assert res.exit_code == 1
    env = _parse(res.output)
    assert env["ok"] is False
    assert env["error"]["type"] == "missing_key"
    assert set(env) == _ENVELOPE_KEYS


def test_json_parse_failure_is_exit_1_with_context(fake_gemini):
    fake_gemini.models.generate_content.return_value = text_response("not json at all")
    res = CliRunner().invoke(cli, ["ask", "--prompt", "hi", "--json"])
    assert res.exit_code == 1
    env = _parse(res.output)
    assert env["ok"] is False
    assert env["error"]["type"] == "json_parse"
    # §5: model/usage still surfaced even though the op failed; no silent fallback.
    assert env["model"]
    assert env["usage"] == {"input_tokens": 5, "output_tokens": 7}


def test_usage_error_is_exit_2(fake_gemini):
    # Missing required --prompt -> Click usage error, exit 2.
    res = CliRunner().invoke(cli, ["ask"])
    assert res.exit_code == 2


def test_exactly_one_json_object_on_stdout(fake_gemini):
    fake_gemini.models.generate_content.return_value = text_response("hello")
    res = CliRunner().invoke(cli, ["ask", "--prompt", "hi"])
    # The whole of stdout must parse as one object (nothing else printed).
    env = json.loads(res.output)
    assert isinstance(env, dict)


def test_api_key_never_appears_in_output(fake_gemini):
    from conftest import SENTINEL_KEY

    fake_gemini.models.generate_content.return_value = text_response("hello")
    res = CliRunner().invoke(cli, ["ask", "--prompt", "hi"])
    assert SENTINEL_KEY not in res.output
