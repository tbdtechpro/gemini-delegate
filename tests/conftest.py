"""Shared fixtures for CLI tests."""
from unittest.mock import MagicMock

import pytest

# A sentinel that must NEVER appear in CLI stdout (CLAUDE.md §3, §5).
SENTINEL_KEY = "TEST-KEY-SENTINEL-do-not-leak"


@pytest.fixture
def fake_gemini(monkeypatch):
    """Patch the client factory so the CLI never touches the network or a key."""
    client = MagicMock()
    monkeypatch.setattr("gemini_delegate.core.make_client", lambda: client)
    monkeypatch.setenv("GEMINI_API_KEY", SENTINEL_KEY)
    return client
