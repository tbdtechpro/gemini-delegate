"""Offline tests for config loading (CLAUDE.md §9.2, §10).

No network, no API key: config is pure file + env logic.
"""
from pathlib import Path

import pytest

from gemini_delegate.config import Config, ConfigError, load_config

# --- env hygiene: these tests must not be perturbed by the real environment ---


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip any gemini-delegate env vars so tests see a known baseline."""
    monkeypatch.delenv("GEMINI_DELEGATE_CONFIG", raising=False)
    for key in list(__import__("os").environ):
        if key.startswith("GEMINI_DELEGATE_MODEL_"):
            monkeypatch.delenv(key, raising=False)
    # Ensure a stray user config never bleeds into the "defaults only" tests.
    monkeypatch.setattr(
        "gemini_delegate.config._user_config_path",
        lambda: Path("/nonexistent/gemini-delegate/config.toml"),
    )


def _write_toml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body)
    return p


# --- packaged-default behavior --------------------------------------------------


def test_packaged_default_resolves_known_roles():
    cfg = load_config()
    # The exact IDs live in config; the contract is that a role resolves to the
    # string configured for it, not a hardcoded constant in source.
    assert cfg.resolve_model("vision") == cfg.models["vision"]
    assert cfg.resolve_model("image_pro") == cfg.models["image_pro"]
    assert cfg.models["vision"]  # non-empty


def test_unknown_token_is_treated_as_explicit_model_id():
    cfg = load_config()
    # Escape hatch (CLAUDE.md §4): not a known role -> passed through verbatim.
    assert cfg.resolve_model("gemini-experimental-xyz") == "gemini-experimental-xyz"


def test_default_role_per_operation():
    cfg = load_config()
    assert cfg.default_role("describe") == "vision"
    assert cfg.default_role("video") == "video"
    assert cfg.default_role("image") == "image"
    assert cfg.default_role("ask") == "text"


def test_value_accessors():
    cfg = load_config()
    assert isinstance(cfg.inline_max_bytes, int)
    assert cfg.inline_max_bytes > 0
    # cache_dir is expanded (no literal ~ left in the path).
    assert "~" not in str(cfg.cache_dir)


# --- override layering ----------------------------------------------------------


def test_explicit_config_merges_over_packaged_default(tmp_path):
    cfg_path = _write_toml(tmp_path, '[models]\nvision = "my-special-vision"\n')
    cfg = load_config(explicit_path=str(cfg_path))
    # Overridden role takes the user's value...
    assert cfg.resolve_model("vision") == "my-special-vision"
    # ...while untouched roles still fall back to the packaged default.
    assert cfg.resolve_model("text")  # present and non-empty


def test_env_config_path_is_honored(tmp_path, monkeypatch):
    cfg_path = _write_toml(tmp_path, '[models]\nimage = "env-image-model"\n')
    monkeypatch.setenv("GEMINI_DELEGATE_CONFIG", str(cfg_path))
    cfg = load_config()
    assert cfg.resolve_model("image") == "env-image-model"


def test_missing_explicit_config_is_an_error(tmp_path):
    missing = tmp_path / "does-not-exist.toml"
    with pytest.raises(ConfigError):
        load_config(explicit_path=str(missing))


def test_env_var_overrides_a_single_model_role(monkeypatch):
    # GEMINI_DELEGATE_MODEL_<ROLE> overrides just that role (CLAUDE.md §9.2).
    monkeypatch.setenv("GEMINI_DELEGATE_MODEL_REASON", "pinned-reason-model")
    cfg = load_config()
    assert cfg.resolve_model("reason") == "pinned-reason-model"
    # Other roles are unaffected.
    assert cfg.resolve_model("text") == cfg.models["text"]


def test_env_var_override_beats_file(tmp_path, monkeypatch):
    cfg_path = _write_toml(tmp_path, '[models]\nvision = "from-file"\n')
    monkeypatch.setenv("GEMINI_DELEGATE_CONFIG", str(cfg_path))
    monkeypatch.setenv("GEMINI_DELEGATE_MODEL_VISION", "from-env")
    cfg = load_config()
    assert cfg.resolve_model("vision") == "from-env"


def test_config_is_a_plain_object():
    cfg = load_config()
    assert isinstance(cfg, Config)


def test_image_pro_resolves_to_current_id():
    cfg = load_config()
    assert cfg.resolve_model("image_pro") == "gemini-3-pro-image"


def test_image_endpoint_defaults_to_auto():
    cfg = load_config()
    assert cfg.image_endpoint == "auto"


def test_image_endpoint_override(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[image]\nendpoint = "interactions"\n')
    cfg = load_config(explicit_path=str(p))
    assert cfg.image_endpoint == "interactions"


def test_search_role_and_default():
    cfg = load_config()
    # `search` is a real configured role (resolves to a model id, not passthrough)
    assert cfg.resolve_model("search") == cfg.models["search"]
    assert cfg.models["search"]  # non-empty
    assert cfg.default_role("search") == "search"
