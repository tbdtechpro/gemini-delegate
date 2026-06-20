# Interactions Image Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Gemini Interactions API (Beta) as the primary image-generation path with an automatic generateContent fallback, plus `--size`/`--aspect-ratio`/`--endpoint` controls and a Pro model-ID fix.

**Architecture:** A new `image_backends.py` module holds an `ImageRequest`, an `ImageBackend` protocol with two implementations (`InteractionsImageBackend`, `GenerateContentImageBackend`), and a `run_image()` dispatcher that applies the endpoint policy (auto/interactions/generate_content) and falls back on failure. `core.image()` becomes a thin wrapper that builds the request, runs the dispatcher, saves bytes, and returns the envelope dict.

**Tech Stack:** Python ≥3.11, `google-genai` 2.9.0 (`client.interactions` + `client.models.generate_content`), `click`, `pillow`. Tests: stdlib + `pytest`, Gemini client mocked.

## Global Constraints

- Run tests in the project venv: `.venv/bin/python -m pytest` (offline, Gemini client mocked, no network, no key).
- Model IDs are config, never source (CLAUDE.md §2.4). Source references logical roles only.
- Every CLI invocation prints exactly one JSON envelope (10 keys: `ok, op, model, text, json, files, session, usage, warnings, error`) to stdout; exit `0/1/2`. Do not add envelope fields — endpoint info goes in `warnings`.
- `core` returns plain dicts; only `cli` knows the envelope/exit codes; only `config` knows model IDs.
- Runtime deps stay `google-genai`, `click`, `pillow` (no new deps).
- API key is resolved by `core.resolve_api_key()` — do not touch key handling.

### Verified SDK signatures (google-genai 2.9.0, pinned 2026-06-20)

- `client.interactions.create(*, model=<str>, input=<list[dict]>, response_format=<dict>, extra_headers=<dict|None>, **body) -> Interaction`
- input blocks: `{"type": "text", "text": <str>}` and `{"type": "image", "data": <base64 str>, "mime_type": <str>}`
- `response_format`: `{"type": "image", "mime_type": "image/png", "image_size": "512|1K|2K|4K", "aspect_ratio": "16:9"}` (size/aspect optional)
- Response extraction: prefer `interaction.output_image.data` if present; else iterate `interaction.steps` → each step's `.content` list → blocks where `block.type == "image"` → `block.data` (base64). Decode with `base64.b64decode`.
- Interactions Beta header: `Api-Revision: 2026-05-20` passed via `extra_headers={"Api-Revision": "2026-05-20"}`.
- generateContent image config: `types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=types.ImageConfig(image_size=..., aspect_ratio=...), candidate_count=n)`. Image bytes at `part.inline_data.data` for each candidate part.
- Pro model ID = `gemini-3-pro-image` (NOT `-preview`). NB2 = `gemini-3.1-flash-image`.

---

### Task 1: Config — Pro model-ID fix + `[image]` endpoint toggle

**Files:**
- Modify: `config/gemini-delegate.toml`
- Modify: `src/gemini_delegate/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.image_endpoint -> str` (returns `[image].endpoint`, default `"auto"`).

- [ ] **Step 1: Write failing tests**

Add to `tests/test_config.py`:

```python
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
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL — `image_pro` is `gemini-3-pro-image-preview`; `Config` has no `image_endpoint`.

- [ ] **Step 3: Fix the model ID in `config/gemini-delegate.toml`**

Change the line:
```toml
image_pro = "gemini-3-pro-image"          # Nano Banana Pro: ~$0.134/img, 4K (premium, opt-in)
```

Add at the end of the file:
```toml
[image]
# Image-generation endpoint: auto = try Interactions (Beta), fall back to
# generateContent on failure. Override per call with --endpoint.
endpoint = "auto"   # auto | interactions | generate_content
```

- [ ] **Step 4: Add the accessor in `src/gemini_delegate/config.py`**

In class `Config`, after `cache_dir`:
```python
    @property
    def image_endpoint(self) -> str:
        return self._data.get("image", {}).get("endpoint", "auto")
```

- [ ] **Step 5: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add config/gemini-delegate.toml src/gemini_delegate/config.py tests/test_config.py
git commit -m "Fix Pro model ID; add [image].endpoint config"
```

---

### Task 2: `ImageRequest` + `run_image` dispatcher (policy + fallback)

**Files:**
- Create: `src/gemini_delegate/image_backends.py`
- Test: `tests/test_image_backends.py`

**Interfaces:**
- Produces:
  - `ImageRequest(prompt: str, model_id: str, refs: list[str]=[], n: int=1, size: str|None=None, aspect_ratio: str|None=None)`
  - `ImageResult(images: list[bytes], usage: dict)`
  - `run_image(client, req, *, policy: str, interactions, generate) -> tuple[ImageResult, str, list[str]]` returning `(result, endpoint_used, warnings)`. `interactions`/`generate` are objects with `.generate(client, req) -> ImageResult`.
- Consumes: `core.CoreError` (for the bad-policy error).

- [ ] **Step 1: Write failing tests**

Create `tests/test_image_backends.py`:

```python
from types import SimpleNamespace

import pytest

from gemini_delegate.core import CoreError
from gemini_delegate.image_backends import ImageRequest, ImageResult, run_image


class _StubBackend:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, 0

    def generate(self, client, req):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


def _req():
    return ImageRequest(prompt="x", model_id="m")


def _res(n=1):
    return ImageResult(images=[b"img"] * n, usage={"input_tokens": 1, "output_tokens": 2})


def test_interactions_policy_uses_interactions_only():
    inter, gen = _StubBackend(_res()), _StubBackend(_res())
    result, endpoint, warnings = run_image(None, _req(), policy="interactions", interactions=inter, generate=gen)
    assert endpoint == "interactions"
    assert gen.calls == 0
    assert warnings == []


def test_generate_content_policy_uses_generate_only():
    inter, gen = _StubBackend(_res()), _StubBackend(_res())
    result, endpoint, warnings = run_image(None, _req(), policy="generate_content", interactions=inter, generate=gen)
    assert endpoint == "generate_content"
    assert inter.calls == 0


def test_auto_falls_back_on_interactions_failure():
    inter = _StubBackend(exc=RuntimeError("beta boom"))
    gen = _StubBackend(_res())
    result, endpoint, warnings = run_image(None, _req(), policy="auto", interactions=inter, generate=gen)
    assert endpoint == "generate_content"
    assert gen.calls == 1
    assert any("fell back" in w and "beta boom" in w for w in warnings)


def test_auto_uses_interactions_when_it_succeeds():
    inter, gen = _StubBackend(_res()), _StubBackend(_res())
    result, endpoint, warnings = run_image(None, _req(), policy="auto", interactions=inter, generate=gen)
    assert endpoint == "interactions"
    assert gen.calls == 0


def test_auto_falls_back_on_empty_result():
    inter = _StubBackend(ImageResult(images=[], usage={}))
    gen = _StubBackend(_res())
    result, endpoint, warnings = run_image(None, _req(), policy="auto", interactions=inter, generate=gen)
    assert endpoint == "generate_content"


def test_interactions_only_propagates_failure():
    inter = _StubBackend(exc=RuntimeError("boom"))
    gen = _StubBackend(_res())
    with pytest.raises(RuntimeError):
        run_image(None, _req(), policy="interactions", interactions=inter, generate=gen)


def test_unknown_policy_raises_core_error():
    with pytest.raises(CoreError) as exc:
        run_image(None, _req(), policy="nonsense", interactions=_StubBackend(), generate=_StubBackend())
    assert exc.value.type == "bad_endpoint"
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/python -m pytest tests/test_image_backends.py -q`
Expected: FAIL — module `gemini_delegate.image_backends` does not exist.

- [ ] **Step 3: Create `src/gemini_delegate/image_backends.py`**

```python
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

from . import media
from .core import CoreError

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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_image_backends.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gemini_delegate/image_backends.py tests/test_image_backends.py
git commit -m "Add ImageRequest + run_image endpoint dispatcher"
```

---

### Task 3: `GenerateContentImageBackend`

**Files:**
- Modify: `src/gemini_delegate/image_backends.py`
- Test: `tests/test_image_backends.py`

**Interfaces:**
- Produces: `GenerateContentImageBackend().generate(client, req) -> ImageResult`. Builds `GenerateContentConfig(response_modalities=["IMAGE"], image_config=ImageConfig(...), candidate_count=n)`, returns `part.inline_data.data` bytes per candidate.
- Consumes: `media.prepare_image_part` (for ref images), `ImageRequest`, `ImageResult`.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_image_backends.py`:

```python
from unittest.mock import MagicMock
import io
from PIL import Image
from gemini_delegate.image_backends import GenerateContentImageBackend


def _png_bytes(color=(10, 20, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


def _gc_response(n_images=1, prompt_tokens=4, cand_tokens=11):
    cands = []
    for i in range(n_images):
        part = SimpleNamespace(inline_data=SimpleNamespace(data=_png_bytes((i, i, i))))
        cands.append(SimpleNamespace(content=SimpleNamespace(parts=[part])))
    return SimpleNamespace(
        candidates=cands,
        usage_metadata=SimpleNamespace(prompt_token_count=prompt_tokens, candidates_token_count=cand_tokens),
    )


def test_generate_content_backend_returns_bytes_and_usage():
    client = MagicMock()
    client.models.generate_content.return_value = _gc_response(1)
    req = ImageRequest(prompt="a cat", model_id="gemini-3.1-flash-image", size="4K", aspect_ratio="16:9")
    result = GenerateContentImageBackend().generate(client, req)
    assert len(result.images) == 1 and isinstance(result.images[0], bytes)
    assert result.usage == {"input_tokens": 4, "output_tokens": 11}
    cfg = client.models.generate_content.call_args.kwargs["config"]
    assert "IMAGE" in [str(m).upper() for m in cfg.response_modalities]
    assert cfg.image_config.image_size == "4K"
    assert cfg.image_config.aspect_ratio == "16:9"


def test_generate_content_backend_candidate_count_for_n():
    client = MagicMock()
    client.models.generate_content.return_value = _gc_response(2)
    req = ImageRequest(prompt="x", model_id="m", n=2)
    result = GenerateContentImageBackend().generate(client, req)
    assert len(result.images) == 2
    cfg = client.models.generate_content.call_args.kwargs["config"]
    assert cfg.candidate_count == 2
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/python -m pytest tests/test_image_backends.py -q`
Expected: FAIL — `GenerateContentImageBackend` undefined.

- [ ] **Step 3: Implement in `src/gemini_delegate/image_backends.py`**

Append:
```python
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
        for ref in req.refs:
            parts.append(
                media.prepare_image_part(
                    client, ref, inline_max_bytes=4 * 1024 * 1024,
                    force_upload=False, cache={},
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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_image_backends.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gemini_delegate/image_backends.py tests/test_image_backends.py
git commit -m "Add GenerateContentImageBackend"
```

---

### Task 4: `InteractionsImageBackend`

**Files:**
- Modify: `src/gemini_delegate/image_backends.py`
- Test: `tests/test_image_backends.py`

**Interfaces:**
- Produces: `InteractionsImageBackend().generate(client, req) -> ImageResult`. Calls `client.interactions.create(model=, input=, response_format=, extra_headers={"Api-Revision": "2026-05-20"})`, extracts image bytes via `output_image` or `steps`.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_image_backends.py`:

```python
from gemini_delegate.image_backends import InteractionsImageBackend


def _interaction_with_steps(payload=b"PNGDATA"):
    b64 = base64.b64encode(payload).decode()
    block = SimpleNamespace(type="image", data=b64)
    step = SimpleNamespace(content=[block])
    return SimpleNamespace(output_image=None, steps=[step],
                           usage=SimpleNamespace(input_tokens=3, output_tokens=9))


def test_interactions_backend_builds_request_and_extracts_image():
    client = MagicMock()
    client.interactions.create.return_value = _interaction_with_steps(b"ABC")
    req = ImageRequest(prompt="a dog", model_id="gemini-3-pro-image", size="4K", aspect_ratio="1:1")
    result = InteractionsImageBackend().generate(client, req)
    assert result.images == [b"ABC"]
    kw = client.interactions.create.call_args.kwargs
    assert kw["model"] == "gemini-3-pro-image"
    assert kw["input"][0] == {"type": "text", "text": "a dog"}
    assert kw["response_format"]["image_size"] == "4K"
    assert kw["response_format"]["aspect_ratio"] == "1:1"
    assert kw["extra_headers"]["Api-Revision"] == "2026-05-20"


def test_interactions_backend_prefers_output_image_when_present():
    client = MagicMock()
    payload = base64.b64encode(b"XYZ").decode()
    client.interactions.create.return_value = SimpleNamespace(
        output_image=SimpleNamespace(data=payload), steps=[])
    result = InteractionsImageBackend().generate(client, ImageRequest(prompt="x", model_id="m"))
    assert result.images == [b"XYZ"]


def test_interactions_backend_loops_for_n():
    client = MagicMock()
    client.interactions.create.return_value = _interaction_with_steps(b"A")
    InteractionsImageBackend().generate(client, ImageRequest(prompt="x", model_id="m", n=3))
    assert client.interactions.create.call_count == 3
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/python -m pytest tests/test_image_backends.py -q`
Expected: FAIL — `InteractionsImageBackend` undefined.

- [ ] **Step 3: Implement in `src/gemini_delegate/image_backends.py`**

Append:
```python
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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_image_backends.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gemini_delegate/image_backends.py tests/test_image_backends.py
git commit -m "Add InteractionsImageBackend"
```

---

### Task 5: Rewire `core.image()` over the backends

**Files:**
- Modify: `src/gemini_delegate/core.py` (replace `image()` body and drop `_save_images`)
- Test: `tests/test_core.py`

**Interfaces:**
- Produces: `core.image(client, cfg, *, prompt, out, refs=(), model=None, n=1, size=None, aspect_ratio=None, endpoint=None) -> dict`. Saves bytes to `out` (numbered for n>1), returns the standard result dict; `warnings` carries the endpoint note.
- Consumes: `image_backends.ImageRequest`, `run_image`, `InteractionsImageBackend`, `GenerateContentImageBackend`.

- [ ] **Step 1: Update existing image tests + add endpoint tests in `tests/test_core.py`**

Replace the existing `_image_response` helper and image tests with bytes-based fakes:

```python
def _gc_image_response(n=1):
    import io
    from PIL import Image
    cands = []
    for i in range(n):
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (0, i * 10, 0)).save(buf, format="PNG")
        part = SimpleNamespace(inline_data=SimpleNamespace(data=buf.getvalue()))
        cands.append(SimpleNamespace(content=SimpleNamespace(parts=[part])))
    return SimpleNamespace(candidates=cands,
                           usage_metadata=SimpleNamespace(prompt_token_count=3, candidates_token_count=7))


def _interaction_response(payload=b"INTERACTIONPNG"):
    import base64
    b64 = base64.b64encode(payload).decode()
    block = SimpleNamespace(type="image", data=b64)
    return SimpleNamespace(output_image=None, steps=[SimpleNamespace(content=[block])],
                           usage=SimpleNamespace(input_tokens=2, output_tokens=5))


def test_image_default_endpoint_uses_interactions(cfg, tmp_path):
    out = tmp_path / "out.png"
    client = MagicMock()
    client.interactions.create.return_value = _interaction_response()
    result = core.image(client, cfg, prompt="draw a cat", out=str(out))
    assert result["op"] == "image"
    assert result["files"] == [str(out.resolve())]
    assert out.is_file()
    client.models.generate_content.assert_not_called()  # interactions handled it
    assert any("interactions" in w for w in result["warnings"])


def test_image_endpoint_generate_content(cfg, tmp_path):
    out = tmp_path / "out.png"
    client = MagicMock()
    client.models.generate_content.return_value = _gc_image_response(1)
    result = core.image(client, cfg, prompt="draw", out=str(out), endpoint="generate_content")
    assert out.is_file()
    client.interactions.create.assert_not_called()


def test_image_auto_falls_back_to_generate_content(cfg, tmp_path):
    out = tmp_path / "out.png"
    client = MagicMock()
    client.interactions.create.side_effect = RuntimeError("beta down")
    client.models.generate_content.return_value = _gc_image_response(1)
    result = core.image(client, cfg, prompt="draw", out=str(out))  # default policy = auto
    assert out.is_file()
    assert any("fell back" in w for w in result["warnings"])


def test_image_interactions_only_failure_raises(cfg, tmp_path):
    client = MagicMock()
    client.interactions.create.side_effect = RuntimeError("boom")
    with pytest.raises(core.CoreError):
        core.image(client, cfg, prompt="x", out=str(tmp_path / "o.png"), endpoint="interactions")


def test_image_size_and_aspect_passed_to_interactions(cfg, tmp_path):
    client = MagicMock()
    client.interactions.create.return_value = _interaction_response()
    core.image(client, cfg, prompt="x", out=str(tmp_path / "o.png"), size="4K", aspect_ratio="16:9", model="image_pro")
    kw = client.interactions.create.call_args.kwargs
    assert kw["model"] == cfg.resolve_model("image_pro")  # gemini-3-pro-image
    assert kw["response_format"]["image_size"] == "4K"


def test_image_no_data_raises(cfg, tmp_path):
    client = MagicMock()
    client.interactions.create.return_value = SimpleNamespace(output_image=None, steps=[],
                                                              usage=SimpleNamespace(input_tokens=0, output_tokens=0))
    client.models.generate_content.return_value = SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[]))],
        usage_metadata=SimpleNamespace(prompt_token_count=0, candidates_token_count=0))
    with pytest.raises(core.CoreError) as exc:
        core.image(client, cfg, prompt="x", out=str(tmp_path / "o.png"))
    assert exc.value.type == "no_image"
```

Delete the old `_image_response`-based tests (`test_image_writes_file`, `test_image_multiple_n_numbers_files`, old `test_image_no_data_raises`).

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/python -m pytest tests/test_core.py -q`
Expected: FAIL — `core.image` has no `endpoint`/`size`/`aspect_ratio` params; still uses old generate_content-only path.

- [ ] **Step 3: Rewrite `image()` in `src/gemini_delegate/core.py`**

Replace the whole `def image(...)` function and remove `_save_images` (no longer used). New body:

```python
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
) -> dict[str, Any]:
    from . import image_backends as ib  # local import avoids a circular import at module load

    model_id = cfg.resolve_model(model or cfg.default_role("image"))
    policy = endpoint or cfg.image_endpoint
    req = ib.ImageRequest(
        prompt=prompt, model_id=model_id, refs=list(refs), n=n, size=size, aspect_ratio=aspect_ratio
    )
    result, used, warnings = ib.run_image(
        client, req, policy=policy,
        interactions=ib.InteractionsImageBackend(), generate=ib.GenerateContentImageBackend(),
    )
    files = _save_image_bytes(result.images, out)
    if not files:
        raise CoreError("no_image", "model returned no image data", details={"model": model_id})
    extras = ", ".join(x for x in (f"size={size}" if size else "", f"aspect={aspect_ratio}" if aspect_ratio else "") if x)
    warnings.append(f"image endpoint: {used}" + (f" ({extras})" if extras else ""))
    return {
        "op": "image", "model": model_id, "text": None, "json": None,
        "files": files, "session": None, "usage": result.usage, "warnings": warnings,
    }
```

Add a bytes-saving helper (replaces `_save_images`):
```python
def _save_image_bytes(images: list[bytes], out: str) -> list[str]:
    out_path = Path(out)
    files: list[str] = []
    for i, data in enumerate(images):
        path = out_path if i == 0 else out_path.with_name(f"{out_path.stem}_{i + 1}{out_path.suffix}")
        path.write_bytes(data)
        files.append(str(path.resolve()))
    return files
```

> Note: `image_backends` imports from `core` (`CoreError`), so `core.image` imports `image_backends` **locally inside the function** to avoid an import cycle at module load.

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_core.py tests/test_image_backends.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gemini_delegate/core.py tests/test_core.py
git commit -m "Rewire core.image over the backend dispatcher"
```

---

### Task 6: CLI options `--size` / `--aspect-ratio` / `--endpoint`

**Files:**
- Modify: `src/gemini_delegate/cli.py` (the `image` command)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `core.image(..., size=, aspect_ratio=, endpoint=)`.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_cli.py` (reuse the existing `image_response` helper for the generate_content path; add an interactions fake):

```python
def _interaction_ok(payload=b"PNG"):
    import base64
    from types import SimpleNamespace
    b64 = base64.b64encode(payload).decode()
    return SimpleNamespace(output_image=None,
                           steps=[SimpleNamespace(content=[SimpleNamespace(type="image", data=b64)])],
                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))


def test_image_size_aspect_endpoint_options(fake_gemini):
    fake_gemini.interactions.create.return_value = _interaction_ok()
    runner = CliRunner()
    with runner.isolated_filesystem():
        res = runner.invoke(cli, ["image", "--prompt", "x", "--out", "o.png",
                                  "--size", "4K", "--aspect-ratio", "16:9", "--endpoint", "interactions"])
        assert res.exit_code == 0
        kw = fake_gemini.interactions.create.call_args.kwargs
        assert kw["response_format"]["image_size"] == "4K"
        import os
        assert os.path.isfile("o.png")


def test_image_rejects_bad_size(fake_gemini):
    res = CliRunner().invoke(cli, ["image", "--prompt", "x", "--out", "o.png", "--size", "8K"])
    assert res.exit_code == 2  # not in choice 512/1K/2K/4K
```

- [ ] **Step 2: Run tests, verify fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: FAIL — `--size`/`--aspect-ratio`/`--endpoint` are unknown options.

- [ ] **Step 3: Add options to the `image` command in `src/gemini_delegate/cli.py`**

Add these decorators to the `image` command (after `--n`):
```python
@click.option("--size", type=click.Choice(["512", "1K", "2K", "4K"]), default=None,
              help="Image resolution (Interactions / Pro). Default: model default.")
@click.option("--aspect-ratio", "aspect_ratio", default=None,
              help="Aspect ratio, e.g. 1:1, 16:9, 4:3.")
@click.option("--endpoint", type=click.Choice(["auto", "interactions", "generate_content"]),
              default=None, help="Override the image endpoint (default from config).")
```

Update the signature and the `run` call:
```python
def image(prompt, prompt_file, out, refs, n, model, size, aspect_ratio, endpoint, debug):
    """Text (+ optional refs) -> generated image file(s)."""
    if n < 1:
        raise click.UsageError("--n must be >= 1")
    prompt = _resolve_prompt(prompt, prompt_file)

    def run(client):
        return core.image(
            client, load_config(), prompt=prompt, out=out, refs=list(refs), model=model, n=n,
            size=size, aspect_ratio=aspect_ratio, endpoint=endpoint,
        )

    _emit("image", debug, run)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (all green)

- [ ] **Step 6: Commit**

```bash
git add src/gemini_delegate/cli.py tests/test_cli.py
git commit -m "Add --size/--aspect-ratio/--endpoint to image command"
```

---

### Task 7: Docs + smoke test + live verify

**Files:**
- Modify: `CLAUDE.md` (amend §2.2 and §13; note in §0)
- Modify: `README.md` (image options + endpoint config)
- Modify: `scripts/smoke_test.py` (opt-in Pro/4K + fallback checks)

**Interfaces:** none (docs + gated script).

- [ ] **Step 1: Amend `CLAUDE.md`**

In §2.2, append: *"Amended 2026-06-20: the `interactions` surface is now the primary path for the `image` op (Beta), with `generate_content` as automatic fallback (config `[image].endpoint`). See `docs/superpowers/specs/2026-06-20-interactions-image-generation-design.md`."* In §13, strike "the `interactions` API" from the out-of-scope list (note the amendment). Add a one-line pointer in §0.

- [ ] **Step 2: Update `README.md`**

In the options table add `--size`, `--aspect-ratio`, `--endpoint`; add a short "Image endpoint (Interactions vs generateContent)" subsection describing `[image].endpoint = auto|interactions|generate_content` and the fallback.

- [ ] **Step 3: Add an opt-in Pro/4K + fallback block to `scripts/smoke_test.py`**

After the existing image check, gated behind an env flag so the default smoke run isn't billed for Pro:
```python
        if os.environ.get("SMOKE_PRO") == "1":
            out_pro = tmpdir / "generated_pro.png"
            code, env, out, err = _call(
                binary,
                ["image", "--model", "image_pro", "--endpoint", "interactions",
                 "--size", "4K", "--aspect-ratio", "16:9",
                 "--prompt", "A single red maple leaf on white, studio lighting",
                 "--out", str(out_pro)],
            )
            results.append(_report("image:pro/interactions/4K", code, env, out, err,
                                   extra_ok=out_pro.is_file() and out_pro.stat().st_size > 0))
            # force the generateContent fallback path
            out_fb = tmpdir / "generated_fallback.png"
            code, env, out, err = _call(
                binary,
                ["image", "--model", "image_pro", "--endpoint", "generate_content",
                 "--prompt", "A single blue maple leaf on white", "--out", str(out_fb)],
            )
            results.append(_report("image:pro/generate_content", code, env, out, err,
                                   extra_ok=out_fb.is_file() and out_fb.stat().st_size > 0))
```

- [ ] **Step 4: Run the full offline suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md README.md scripts/smoke_test.py
git commit -m "Docs + smoke test for Interactions image path"
```

- [ ] **Step 6: Live verify (run by the user / gated — not part of `make test`)**

Resolve the two live-only risks here:
```bash
# Pro 4K via Interactions to the target dir, plus the fallback path:
mkdir -p /home/matt/Pictures/NB2P
RUN_LIVE=1 SMOKE_PRO=1 .venv/bin/python scripts/smoke_test.py
```
Confirm both checks PASS and the envelope `model` reads `gemini-3-pro-image`. **If the Interactions call errors** (e.g. the `Api-Revision` header is rejected or the request shape differs), capture the error envelope and adjust `InteractionsImageBackend` (header via `extra_headers`, or the `input`/`response_format` shape) — the offline tests pin the extraction logic, so only the request construction would change.

---

## Self-Review

**Spec coverage:** scope (image-only, extensible seam) → Tasks 2–5; endpoint config + `--endpoint` → Tasks 1, 5, 6; `--size`/`--aspect-ratio` → Tasks 3, 4, 6; Pro model-ID fix → Task 1; fallback + warnings → Tasks 2, 5; envelope unchanged → Task 5; tests → every task; docs + live verify → Task 7. All spec sections covered.

**Placeholder scan:** no TBD/TODO; every code step shows complete code; the live-verify step (7.6) is intentionally manual + gated, with a concrete command and an explicit adjust-if-fails instruction.

**Type consistency:** `ImageRequest`/`ImageResult` fields and `run_image`/backend `generate(client, req)` signatures are identical across Tasks 2–6; `core.image(..., size=, aspect_ratio=, endpoint=)` matches the CLI call in Task 6; `image_endpoint` (Task 1) is consumed in Task 5.
