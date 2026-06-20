"""Offline tests for API-key resolution (env, then dotenv-style key file).

The key is read from the environment first, then a key file — never from the
command line, never logged (CLAUDE.md §3, amended to allow a user key file).
"""
import pytest

from gemini_delegate.core import CoreError, make_client, resolve_api_key


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # No ambient key from env or a real ~/.config file should leak in.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_DELEGATE_ENV", raising=False)
    monkeypatch.setattr(
        "gemini_delegate.core._default_key_file", lambda: tmp_path / "absent.env"
    )


def test_env_var_takes_precedence(monkeypatch, tmp_path):
    f = tmp_path / "k.env"
    f.write_text("GEMINI_API_KEY=from-file\n")
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    monkeypatch.setenv("GEMINI_DELEGATE_ENV", str(f))
    assert resolve_api_key() == "from-env"


def test_loads_from_env_file_when_var_absent(monkeypatch, tmp_path):
    f = tmp_path / "k.env"
    f.write_text("GEMINI_API_KEY=abc123\n")
    monkeypatch.setenv("GEMINI_DELEGATE_ENV", str(f))
    assert resolve_api_key() == "abc123"


def test_loads_from_default_key_file(monkeypatch, tmp_path):
    f = tmp_path / "default.env"
    f.write_text("GEMINI_API_KEY=defkey\n")
    monkeypatch.setattr("gemini_delegate.core._default_key_file", lambda: f)
    assert resolve_api_key() == "defkey"


def test_parses_export_prefix_and_quotes(monkeypatch, tmp_path):
    f = tmp_path / "k.env"
    f.write_text('# a comment\n\nexport GEMINI_API_KEY="quoted-key"\nOTHER=x\n')
    monkeypatch.setenv("GEMINI_DELEGATE_ENV", str(f))
    assert resolve_api_key() == "quoted-key"


def test_ignores_unrelated_keys(monkeypatch, tmp_path):
    f = tmp_path / "k.env"
    f.write_text("OTHER_KEY=nope\nGEMINI_API_KEY_SUFFIX=no\n")
    monkeypatch.setenv("GEMINI_DELEGATE_ENV", str(f))
    assert resolve_api_key() is None


def test_missing_everywhere_returns_none():
    assert resolve_api_key() is None


def test_make_client_raises_missing_key_when_nothing_found():
    with pytest.raises(CoreError) as exc:
        make_client()
    assert exc.value.type == "missing_key"
