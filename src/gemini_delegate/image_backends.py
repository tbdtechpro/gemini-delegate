"""Image-generation backends + endpoint dispatcher (CLAUDE.md image spec).

Two backends implement the same `generate(client, req) -> ImageResult` shape:
the Interactions API (Beta, primary) and generateContent (stable, fallback).
`run_image` applies the endpoint policy and falls back in `auto` mode. The
try/fallback shape is deliberately simple and op-agnostic so other operations
can adopt the same pattern later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .core import CoreError


@dataclass
class ImageRequest:
    prompt: str
    model_id: str
    refs: list[str] = field(default_factory=list)
    n: int = 1
    size: str | None = None
    aspect_ratio: str | None = None


@dataclass
class ImageResult:
    images: list[bytes]
    usage: dict[str, int]


def run_image(
    client: Any,
    req: ImageRequest,
    *,
    policy: str,
    interactions: Any,
    generate: Any,
) -> tuple[ImageResult, str, list[str]]:
    """Run image generation per the endpoint policy; return (result, endpoint, warnings)."""
    warnings: list[str] = []
    if policy == "generate_content":
        return generate.generate(client, req), "generate_content", warnings
    if policy == "interactions":
        return interactions.generate(client, req), "interactions", warnings
    if policy == "auto":
        try:
            result = interactions.generate(client, req)
            if result.images:
                return result, "interactions", warnings
            reason = "no image returned"
        except Exception as exc:  # noqa: BLE001 — any Beta failure should fall back
            reason = f"{type(exc).__name__}: {exc}"
        warnings.append(f"interactions failed ({reason}); fell back to generate_content")
        return generate.generate(client, req), "generate_content", warnings
    raise CoreError("bad_endpoint", f"unknown image endpoint policy: {policy!r}")
