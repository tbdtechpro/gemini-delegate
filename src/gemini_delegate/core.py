"""All Gemini logic for the four operations (CLAUDE.md §6).

Each public function returns a plain dict; only the CLI knows about the JSON
envelope and exit codes. Failures raise ``CoreError`` (carrying enough context
— model, usage, raw text — for the CLI to still populate the envelope).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Sequence

from google import genai
from google.genai import types

from . import media
from .config import Config
from .session import Session

_JSON_MIME = "application/json"

_TRANSPARENT_DIRECTIVE = (
    "Render the entire subject on a solid, flat, uniform {name} ({hexv}) background "
    "— one single flat fill color, with no other background elements, no checkerboard "
    "or transparency pattern, no gradient, no texture, and no shadow on the background."
)


class CoreError(Exception):
    """A failure inside a core operation, with optional envelope context."""

    def __init__(self, type: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.type = type
        self.message = message
        self.details = details or {}


def _default_key_file() -> Path:
    """User-scope key file used when GEMINI_API_KEY is not in the environment."""
    return Path("~/.config/gemini-delegate/.env").expanduser()


def _read_key_from_file(path: Path) -> str | None:
    """Pull GEMINI_API_KEY from a dotenv-style file; never logged."""
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, sep, value = line.partition("=")
        if sep and name.strip() == "GEMINI_API_KEY":
            value = value.strip().strip('"').strip("'")
            if value:
                return value
    return None


def resolve_api_key() -> str | None:
    """Resolve the key: env var first, then $GEMINI_DELEGATE_ENV, then the
    user key file (CLAUDE.md §3, amended to allow a user-controlled key file so
    every session/subagent gets it with zero discovery). Never on the command
    line, never logged."""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    candidates: list[Path] = []
    env_file = os.environ.get("GEMINI_DELEGATE_ENV")
    if env_file:
        candidates.append(Path(env_file).expanduser())
    candidates.append(_default_key_file())
    for path in candidates:
        key = _read_key_from_file(path)
        if key:
            return key
    return None


def make_client() -> genai.Client:
    """One client; the key is resolved from env or a user key file (never logged)."""
    key = resolve_api_key()
    if not key:
        raise CoreError(
            "missing_key",
            "GEMINI_API_KEY not found in the environment, $GEMINI_DELEGATE_ENV, "
            "or ~/.config/gemini-delegate/.env",
        )
    return genai.Client(api_key=key)


# --- public operations ----------------------------------------------------------


def describe(
    client: Any,
    cfg: Config,
    *,
    images: Sequence[str],
    prompt: str,
    model: str | None = None,
    want_json: bool = False,
    schema: str | None = None,
    session_path: str | None = None,
) -> dict[str, Any]:
    def build(force_upload: bool, cache: dict, warnings: list[str]) -> list[types.Part]:
        parts = [
            media.prepare_image_part(
                client, img, inline_max_bytes=cfg.inline_max_bytes,
                force_upload=force_upload, cache=cache, warnings=warnings,
            )
            for img in images
        ]
        parts.append(types.Part(text=prompt))
        return parts

    return _run_text_op(
        client, cfg, op="describe", build_parts=build, model=model,
        want_json=want_json, schema=schema, session_path=session_path,
    )


def video(
    client: Any,
    cfg: Config,
    *,
    src: str,
    prompt: str,
    model: str | None = None,
    want_json: bool = False,
    schema: str | None = None,
    session_path: str | None = None,
) -> dict[str, Any]:
    def build(force_upload: bool, cache: dict, warnings: list[str]) -> list[types.Part]:
        # Video always uploads (or passes a URL through); force_upload is moot,
        # but the shared cache keeps multi-turn cheap.
        return [
            media.prepare_video_part(client, src, cache=cache, warnings=warnings),
            types.Part(text=prompt),
        ]

    return _run_text_op(
        client, cfg, op="video", build_parts=build, model=model,
        want_json=want_json, schema=schema, session_path=session_path,
    )


def ask(
    client: Any,
    cfg: Config,
    *,
    prompt: str,
    model: str | None = None,
    want_json: bool = False,
    schema: str | None = None,
    session_path: str | None = None,
) -> dict[str, Any]:
    def build(force_upload: bool, cache: dict, warnings: list[str]) -> list[types.Part]:
        return [types.Part(text=prompt)]

    return _run_text_op(
        client, cfg, op="ask", build_parts=build, model=model,
        want_json=want_json, schema=schema, session_path=session_path,
    )


def image(
    client: Any,
    cfg: Config,
    *,
    prompt: str,
    out: str,
    refs: Sequence[str] = (),
    model: str | None = None,
    n: int = 1,
    size: str | None = None,
    aspect_ratio: str | None = None,
    endpoint: str | None = None,
    transparent: bool = False,
    chroma_key: str | None = None,
    chroma_tolerance: int = 60,
    keep_original: bool = False,
) -> dict[str, Any]:
    from . import image_backends as ib
    from . import imaging

    model_id = cfg.resolve_model(model or cfg.default_role("image"))
    policy = endpoint or cfg.image_endpoint

    do_key = transparent or chroma_key is not None
    key_rgb = None
    if do_key:
        key_rgb = imaging.parse_color(chroma_key) if chroma_key else (255, 0, 255)
    if transparent:
        name = chroma_key or "magenta"
        hexv = "#%02X%02X%02X" % key_rgb
        prompt = prompt + "\n\n" + _TRANSPARENT_DIRECTIVE.format(name=name, hexv=hexv)

    req = ib.ImageRequest(
        prompt=prompt, model_id=model_id, refs=list(refs), n=n, size=size, aspect_ratio=aspect_ratio
    )
    try:
        result, used, warnings = ib.run_image(
            client, req, policy=policy,
            interactions=ib.InteractionsImageBackend(), generate=ib.GenerateContentImageBackend(),
        )
    except CoreError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CoreError("image_error", str(exc), details={"model": model_id}) from exc

    files = _render_outputs(result.images, out, key_rgb, chroma_tolerance, keep_original, warnings)
    if not files:
        raise CoreError("no_image", "model returned no image data", details={"model": model_id})

    extras = ", ".join(x for x in (f"size={size}" if size else "", f"aspect={aspect_ratio}" if aspect_ratio else "") if x)
    warnings.append(f"image endpoint: {used}" + (f" ({extras})" if extras else ""))
    return {
        "op": "image", "model": model_id, "text": None, "json": None,
        "files": files, "session": None, "usage": result.usage, "warnings": warnings,
    }


# --- shared text-op machinery ---------------------------------------------------


def _run_text_op(
    client: Any,
    cfg: Config,
    *,
    op: str,
    build_parts: Callable[[bool, dict, list[str]], list[types.Part]],
    model: str | None,
    want_json: bool,
    schema: str | None,
    session_path: str | None,
) -> dict[str, Any]:
    warnings: list[str] = []
    role = model or cfg.default_role(op)
    model_id = cfg.resolve_model(role)
    config, want_json = _build_config(want_json, schema)

    if session_path:
        session: Session | None = Session.load_or_create(session_path, role)
        new_parts = build_parts(True, session.uploads, warnings)
        session.append_user([_dump(p) for p in new_parts])
        contents = [types.Content.model_validate(c) for c in session.contents]
    else:
        session = None
        new_parts = build_parts(False, {}, warnings)
        contents = [types.Content(role="user", parts=new_parts)]

    resp = client.models.generate_content(model=model_id, contents=contents, config=config)
    text = resp.text
    usage = _usage(resp)
    json_obj = _parse_json(text, model_id, usage) if want_json else None

    if session is not None:
        session.append_model([_dump(p) for p in _response_parts(resp)])
        session.save(session_path)

    return {
        "op": op, "model": model_id, "text": text, "json": json_obj,
        "files": [], "session": str(Path(session_path).resolve()) if session_path else None,
        "usage": usage, "warnings": warnings,
    }


def _build_config(
    want_json: bool, schema: str | None
) -> tuple[types.GenerateContentConfig | None, bool]:
    if schema:
        return (
            types.GenerateContentConfig(
                response_mime_type=_JSON_MIME, response_schema=_load_schema(schema)
            ),
            True,
        )
    if want_json:
        return types.GenerateContentConfig(response_mime_type=_JSON_MIME), True
    return None, False


def _load_schema(path: str) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CoreError("bad_schema", f"could not read JSON schema {path}: {exc}") from exc


def _parse_json(text: str | None, model_id: str, usage: dict[str, int]) -> Any:
    try:
        return json.loads(text)  # type: ignore[arg-type]
    except (json.JSONDecodeError, TypeError) as exc:
        # CLAUDE.md §5: never silently fall back to text on a JSON-parse failure.
        raise CoreError(
            "json_parse",
            f"model output was not valid JSON: {exc}",
            details={"model": model_id, "usage": usage, "text": text},
        ) from exc


def _dump(part: types.Part) -> dict[str, Any]:
    return part.model_dump(mode="json", exclude_none=True)


def _response_parts(resp: Any) -> list[types.Part]:
    try:
        parts = resp.candidates[0].content.parts
        if parts:
            return list(parts)
    except (AttributeError, IndexError, TypeError):
        pass
    return [types.Part(text=getattr(resp, "text", "") or "")]


def _render_outputs(
    images: list[bytes], out: str, key_rgb: tuple[int, int, int] | None,
    tolerance: int, keep_original: bool, warnings: list[str],
) -> list[str]:
    """Decode API bytes; chroma-key when key_rgb is set, else transcode; save in the
    --out format. Appends keying warnings; optionally keeps the un-keyed original."""
    from . import imaging

    out_path = Path(out)
    files: list[str] = []
    for i, data in enumerate(images):
        path = out_path if i == 0 else out_path.with_name(f"{out_path.stem}_{i + 1}{out_path.suffix}")
        try:
            img = imaging.decode(data)
            if key_rgb is not None:
                keyed, stats = imaging.chroma_key(img, key_rgb, tolerance)
                warnings.extend(imaging.validate_key(stats))
                files.append(imaging.save_image(keyed, str(path)))
                if keep_original:
                    imaging.save_image(img, str(path.with_name(f"{path.stem}.orig.jpg")))
            else:
                files.append(imaging.save_image(img, str(path)))
        except imaging.ImagingError as exc:
            raise CoreError("image_error", str(exc), details={}) from exc
    return files


def _usage(resp: Any) -> dict[str, int]:
    um = getattr(resp, "usage_metadata", None)
    if um is None:
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": getattr(um, "prompt_token_count", 0) or 0,
        "output_tokens": getattr(um, "candidates_token_count", 0) or 0,
    }
