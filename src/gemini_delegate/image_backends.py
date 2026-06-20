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

from google.genai import types

from .core import CoreError
from . import media


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


def _gc_usage(resp: Any) -> dict[str, int]:
    um = getattr(resp, "usage_metadata", None)
    if um is None:
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": getattr(um, "prompt_token_count", 0) or 0,
        "output_tokens": getattr(um, "candidates_token_count", 0) or 0,
    }


class GenerateContentImageBackend:
    name = "generate_content"

    def generate(self, client: Any, req: ImageRequest) -> ImageResult:
        config = types.GenerateContentConfig(response_modalities=["IMAGE"])
        if req.size or req.aspect_ratio:
            config.image_config = types.ImageConfig(
                image_size=req.size, aspect_ratio=req.aspect_ratio
            )
        if req.n and req.n > 1:
            config.candidate_count = req.n
        parts: list[types.Part] = [types.Part(text=req.prompt)]
        cache: dict = {}
        for ref in req.refs:
            parts.append(
                media.prepare_image_part(
                    client, ref, inline_max_bytes=4 * 1024 * 1024,
                    force_upload=False, cache=cache,
                )
            )
        resp = client.models.generate_content(
            model=req.model_id,
            contents=[types.Content(role="user", parts=parts)],
            config=config,
        )
        images = [
            part.inline_data.data
            for cand in (getattr(resp, "candidates", None) or [])
            for part in (getattr(cand.content, "parts", None) or [])
            if getattr(part, "inline_data", None)
        ]
        return ImageResult(images=images, usage=_gc_usage(resp))
