"""Configuration loading and model-role resolution (CLAUDE.md §2.4, §6, §9.2).

Model IDs are config, never code. Source only ever asks for a logical *role*
(``text``, ``vision``, ``video``, ``image``, ``image_pro``, ``reason``); this
module turns a role into a concrete model ID, falling back to treating an
unknown token as an explicit model ID (the escape hatch in §4).

Resolution order for the config *file* (§9.2):
    1. ``$GEMINI_DELEGATE_CONFIG``                       (explicit; error if missing)
    2. ``~/.config/gemini-delegate/config.toml``         (per-user, optional)
    3. packaged default                                  (always present)

A higher-priority file is deep-merged *over* the packaged default, so a user
config only needs to specify the keys it wants to change. Finally,
``GEMINI_DELEGATE_MODEL_<ROLE>`` env vars override individual model roles —
handy for pinning a model for one run without editing any file (model IDs
churn; §12).
"""
from __future__ import annotations

import importlib.resources
import os
import tomllib
from pathlib import Path
from typing import Any

_ENV_CONFIG_PATH = "GEMINI_DELEGATE_CONFIG"
_ENV_MODEL_PREFIX = "GEMINI_DELEGATE_MODEL_"


class ConfigError(Exception):
    """Raised on an unusable configuration (missing file, malformed, etc.)."""


class Config:
    """Parsed configuration with role resolution.

    Holds a plain dict; only thin typed accessors are exposed so the rest of
    the code never reaches into raw TOML structure.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @property
    def models(self) -> dict[str, str]:
        return dict(self._data.get("models", {}))

    def resolve_model(self, role_or_id: str) -> str:
        """Map a logical role to its model ID, or pass an explicit ID through."""
        return self._data.get("models", {}).get(role_or_id, role_or_id)

    def default_role(self, op: str) -> str:
        """The default logical role for a subcommand (CLAUDE.md §4)."""
        try:
            return self._data["defaults"][op]
        except KeyError as exc:
            raise ConfigError(f"no default role configured for operation {op!r}") from exc

    @property
    def inline_max_bytes(self) -> int:
        return int(self._data["media"]["inline_max_bytes"])

    @property
    def cache_dir(self) -> Path:
        return Path(self._data["paths"]["cache_dir"]).expanduser()


def load_config(explicit_path: str | None = None) -> Config:
    """Load config following the resolution order, then apply env overrides."""
    data = _load_toml(_packaged_default_path())
    user = _select_user_config(explicit_path)
    if user is not None:
        _deep_merge(data, _load_toml(user))
    _apply_env_overrides(data)
    return Config(data)


# --- internals ------------------------------------------------------------------


def _user_config_path() -> Path:
    """Per-user config location (overridable in tests)."""
    return Path.home() / ".config" / "gemini-delegate" / "config.toml"


def _packaged_default_path() -> Path:
    """Locate the packaged default config across install modes (CLAUDE.md §3).

    Editable installs run from ``src/``, where the wheel's bundled data does not
    exist, so we first look for the repo-root ``config/`` relative to this file;
    a real wheel install instead carries the file as package data.
    """
    editable = Path(__file__).resolve().parents[2] / "config" / "gemini-delegate.toml"
    if editable.is_file():
        return editable
    bundled = importlib.resources.files("gemini_delegate") / "data" / "gemini-delegate.toml"
    if bundled.is_file():
        return Path(str(bundled))
    raise ConfigError("packaged default config not found")


def _select_user_config(explicit_path: str | None) -> Path | None:
    """Pick the highest-priority user config file, or None to use defaults only."""
    if explicit_path is not None:
        p = Path(explicit_path)
        if not p.is_file():
            raise ConfigError(f"config file not found: {explicit_path}")
        return p
    env_path = os.environ.get(_ENV_CONFIG_PATH)
    if env_path:
        p = Path(env_path)
        if not p.is_file():
            raise ConfigError(f"{_ENV_CONFIG_PATH} points to a missing file: {env_path}")
        return p
    user = _user_config_path()
    return user if user.is_file() else None


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"could not read config {path}: {exc}") from exc


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> None:
    """Recursively merge ``overlay`` into ``base`` in place."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _apply_env_overrides(data: dict[str, Any]) -> None:
    """Apply ``GEMINI_DELEGATE_MODEL_<ROLE>`` env overrides onto [models]."""
    models = data.setdefault("models", {})
    for key, value in os.environ.items():
        if key.startswith(_ENV_MODEL_PREFIX):
            role = key[len(_ENV_MODEL_PREFIX):].lower()
            models[role] = value
