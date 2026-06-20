"""Image-generation backends + endpoint dispatcher (CLAUDE.md image spec).

Two backends implement the same `generate(client, req) -> ImageResult` shape:
the Interactions API (Beta, primary) and generateContent (stable, fallback).
`run_image` applies the endpoint policy and falls back in `auto` mode. The
try/fallback shape is deliberately simple and op-agnostic so other operations
can adopt the same pattern later.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google.genai import types

from .core import CoreError
from . import media

_API_REVISION = "2026-05-20"


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


def _extract_interaction_images(interaction: Any) -> list[bytes]:
    out_img = getattr(interaction, "output_image", None)
    if out_img is not None and getattr(out_img, "data", None):
        return [base64.b64decode(out_img.data)]
    images: list[bytes] = []
    for step in getattr(interaction, "steps", None) or []:
        for block in getattr(step, "content", None) or []:
            if getattr(block, "type", None) == "image" and getattr(block, "data", None):
                images.append(base64.b64decode(block.data))
    return images


def _interaction_usage(interaction: Any) -> dict[str, int]:
    u = getattr(interaction, "usage", None)
    if u is None:
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": getattr(u, "input_tokens", 0) or 0,
        "output_tokens": getattr(u, "output_tokens", 0) or 0,
    }


class InteractionsImageBackend:
    name = "interactions"

    def generate(self, client: Any, req: ImageRequest) -> ImageResult:
        response_format: dict[str, Any] = {"type": "image", "mime_type": "image/png"}
        if req.size:
            response_format["image_size"] = req.size
        if req.aspect_ratio:
            response_format["aspect_ratio"] = req.aspect_ratio
        blocks: list[dict[str, Any]] = [{"type": "text", "text": req.prompt}]
        for ref in req.refs:
            data = base64.b64encode(Path(ref).read_bytes()).decode()
            blocks.append({
                "type": "image", "data": data,
                "mime_type": media.guess_mime(ref) or "image/png",
            })
        images: list[bytes] = []
        usage = {"input_tokens": 0, "output_tokens": 0}
        for _ in range(max(1, req.n)):
            interaction = client.interactions.create(
                model=req.model_id,
                input=blocks,
                response_format=response_format,
                extra_headers={"Api-Revision": _API_REVISION},
            )
            images.extend(_extract_interaction_images(interaction))
            u = _interaction_usage(interaction)
            usage["input_tokens"] += u["input_tokens"]
            usage["output_tokens"] += u["output_tokens"]
        return ImageResult(images=images, usage=usage)
