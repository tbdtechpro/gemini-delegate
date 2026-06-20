"""Shared fixtures for CLI tests."""
from unittest.mock import MagicMock

import pytest

# A sentinel that must NEVER appear in CLI stdout (CLAUDE.md §3, §5).
SENTINEL_KEY = "TEST-KEY-SENTINEL-do-not-leak"


@pytest.fixture(autouse=True)
def _isolate_key_file(monkeypatch, tmp_path_factory):
    """Keep tests hermetic: a real ~/.config/gemini-delegate/.env on the dev
    machine must not leak into key resolution. Only neutralizes the *file*
    fallback — GEMINI_API_KEY in the env is left alone (fixtures/tests set it)."""
    monkeypatch.delenv("GEMINI_DELEGATE_ENV", raising=False)
    absent = tmp_path_factory.mktemp("nokey") / "absent.env"
    monkeypatch.setattr("gemini_delegate.core._default_key_file", lambda: absent)


@pytest.fixture
def fake_gemini(monkeypatch):
    """Patch the client factory so the CLI never touches the network or a key."""
    client = MagicMock()
    monkeypatch.setattr("gemini_delegate.core.make_client", lambda: client)
    monkeypatch.setenv("GEMINI_API_KEY", SENTINEL_KEY)
    return client
