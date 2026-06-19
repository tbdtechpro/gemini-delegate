"""Multi-turn session persistence (CLAUDE.md §6).

`generate_content` is stateless, so the `contents` list *is* the memory. A
session file is plain JSON holding the model role, that running `contents` list
(`[{role, parts}]` with already-serialized part dicts), and the upload cache
(``sha256 -> {uri, mime, name, expires}``) so media isn't re-uploaded across
turns. This module is deliberately SDK-free: it moves dicts, nothing more.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SessionError(Exception):
    """Raised when a session file cannot be read or is malformed."""


@dataclass
class Session:
    role: str
    contents: list[dict[str, Any]] = field(default_factory=list)
    uploads: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def new(cls, role: str) -> "Session":
        return cls(role=role)

    @classmethod
    def load(cls, path: str | Path) -> "Session":
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionError(f"could not read session {path}: {exc}") from exc
        if not isinstance(data, dict) or "role" not in data:
            raise SessionError(f"malformed session file {path}: missing 'role'")
        return cls(
            role=data["role"],
            contents=data.get("contents", []),
            uploads=data.get("uploads", {}),
        )

    @classmethod
    def load_or_create(cls, path: str | Path, role: str) -> "Session":
        """Load an existing session, or start a fresh one for the first turn."""
        return cls.load(path) if Path(path).is_file() else cls.new(role)

    def append_user(self, parts: list[dict[str, Any]]) -> None:
        self.contents.append({"role": "user", "parts": parts})

    def append_model(self, parts: list[dict[str, Any]]) -> None:
        self.contents.append({"role": "model", "parts": parts})

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "contents": self.contents, "uploads": self.uploads}

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))
