# Design: Interactions endpoint for image generation (with generateContent fallback)

**Date:** 2026-06-20
**Status:** Approved (brainstorm), pending spec review → implementation plan
**Repo:** gemini-delegate (`/home/matt/github/GemCLI-Helper`)

## Summary

Add the Gemini **Interactions** API (Beta) as the **primary** path for the
`image` (text→image) operation, with the existing `generateContent` path as an
automatic **fallback**. Expose the resolution/aspect controls Interactions
offers (up to 4K), make the endpoint choice configurable with a per-call CLI
override, and fix a stale Pro model ID. Other operations (describe/video/ask)
stay on `generateContent`; the seam is built so they *could* adopt Interactions
later without rework, but no other op is wired now (YAGNI).

## Motivation

- The Interactions API is a closer match to a multimodal, multi-turn tool and
  natively exposes image `image_size` (512/1K/2K/**4K**) and `aspect_ratio` —
  the headline reason to use Nano Banana Pro.
- It is **Beta** and a "moving target," so we keep `generateContent` (the stable
  path the Google docs recommend for production) as a fallback and a one-line
  config escape hatch.
- This consciously **amends the original charter** (§2.2 "do not use the
  `interactions` surface"; §13 listed it out of scope). The repo is a WIP
  personal experiment, so the amendment is acceptable; it is recorded here and
  in CLAUDE.md.

## Verified facts (installed SDK + Google docs, 2026-06-20)

- `google-genai 2.9.0` ships an `interactions` surface (`client.interactions`).
  No SDK upgrade required.
- Model IDs (both the Interactions and generateContent docs agree):
  - Nano Banana Pro = **`gemini-3-pro-image`**
  - Nano Banana 2 = **`gemini-3.1-flash-image`**
  - Our config's `image_pro = "gemini-3-pro-image-preview"` is **stale** and
    would 404 today; this design fixes it to `gemini-3-pro-image`.
- Interactions image-gen shape (per docs; exact SDK signatures to be pinned at
  implementation start — see Risks):
  - `client.interactions.create(model=…, input=[{"type":"text","text":…}, {"type":"image","data":<b64>,"mime_type":…}], response_format={"type":"image","mime_type":…,"image_size":"4K","aspect_ratio":"16:9"})`
  - Result image bytes at `interaction.output_image.data` (base64).
  - HTTP requires header `Api-Revision: 2026-05-20`.

## Scope

**In scope:** the `image` op gains Interactions-primary + generateContent
fallback, `--size`/`--aspect-ratio`/`--endpoint` options, a config endpoint
toggle, and the Pro model-ID fix.

**Out of scope (now):** moving describe/video/ask to Interactions; streaming;
Interactions tools (search, etc.); multi-turn image sessions. The dispatcher
seam is op-agnostic so these are cheap to add later, but none are built.

## Architecture

New module `src/gemini_delegate/image_backends.py` holding the seam:

- **`ImageRequest`** (dataclass): `prompt: str`, `refs: list[str]`, `n: int`,
  `model_id: str`, `size: str | None`, `aspect_ratio: str | None`.
- **`ImageBackend`** (Protocol): `generate(client, req) -> list[bytes]` —
  returns raw image bytes (one entry per generated image).
- **`InteractionsImageBackend`** — builds the `input` list (text + base64 ref
  images) and `response_format` (size/aspect), calls
  `client.interactions.create(...)`, decodes `output_image.data` (and iterates
  `steps` if multiple images). Honors `n` by looping the call when the API
  returns one image per call.
- **`GenerateContentImageBackend`** — the current `generate_content` path
  refactored out of `core.image`: `response_modalities=["IMAGE"]` plus a
  `response_format` carrying size/aspect when set; `candidate_count=n` for n>1;
  reads `part.inline_data`/`as_image()`.
- **`select_and_run(policy, primary, fallback) -> (bytes_list, endpoint, warnings)`**
  — op-agnostic dispatcher: runs `primary`; in `auto` mode, on any exception or
  empty result, runs `fallback` and records the reason. Returns which endpoint
  produced the result.

`core.image()` becomes a thin wrapper: resolve model + endpoint policy → build
`ImageRequest` → choose backend(s) → `select_and_run` → save bytes to `--out`
(numbered for n>1) → return the plain result dict (adding endpoint/size notes to
`warnings`). `core.py` keeps owning file I/O and the dict shape; the backends
own only "request → image bytes."

## Configuration

`config/gemini-delegate.toml`:

```toml
[models]
# ...
image_pro = "gemini-3-pro-image"   # was gemini-3-pro-image-preview (stale)

[image]
endpoint = "auto"                  # auto | interactions | generate_content
```

`config.py`: add `Config.image_endpoint -> str` returning `[image].endpoint` or
`"auto"` when absent. Unknown values are treated as a config error at use time
(validated when resolving the policy).

## CLI

`image` command gains:

- `--size` — `click.Choice(["512","1K","2K","4K"])`, default `None` (model default).
- `--aspect-ratio TEXT` — free string (e.g. `16:9`, `1:1`); the API validates.
- `--endpoint` — `click.Choice(["auto","interactions","generate_content"])`,
  default `None` (falls through to config).

Unchanged: `--prompt`/`--prompt-file`, `--out`, `--ref`, `--n`, `--model`.

## Endpoint selection & fallback

Resolved policy = `--endpoint` › `[image].endpoint` › `"auto"`.

| policy | behavior |
|---|---|
| `interactions` | Interactions only; failure → error envelope (exit 1), no fallback |
| `generate_content` | generateContent only |
| `auto` | try Interactions; on **any** failure (exception, no image, or SDK lacks `interactions`) → generateContent, with a `warnings` entry naming the fallback + reason |

If both paths fail under `auto`, return an error envelope (`ok:false`, exit 1)
whose error reflects the fallback's failure.

## Output envelope

Unchanged 10-key schema (CLAUDE.md §5). The endpoint actually used and the
size/aspect requested are reported via `warnings`, e.g.
`"image endpoint: interactions (size=4K, aspect=16:9)"`; on fallback a second
entry explains why. `model` already carries the resolved model ID. No new
envelope fields.

## Error handling

All failures flow through the existing `CoreError` → CLI boundary: clean
`ok:false` envelope, exit 1, no traceback to stdout (`--debug` → stderr).
A backend that returns zero images raises `no_image` as today.

## Testing (TDD, offline, mocked client)

New `tests/test_image_backends.py` and updates to `test_core`/`test_cli`/`test_config`:

- Interactions backend builds the correct `input` (prompt + base64 refs) and
  `response_format` (size + aspect), and saves bytes from `output_image.data`.
- generateContent backend path selected by `--endpoint generate_content`.
- **auto fallback:** `interactions.create` raises → generateContent used, a
  warning records the fallback.
- **interactions-only:** failure → error envelope, exit 1 (no fallback).
- size/aspect propagate into the request for both paths.
- `--endpoint` overrides config; config `[image].endpoint` default = `auto`.
- `image_pro` resolves to `gemini-3-pro-image`.
- SDK-missing-`interactions` guard: simulate no `interactions` attr → `auto`
  falls back; `interactions`-only → error.

Gated live verify (`RUN_LIVE=1`): generate one Pro 4K image via Interactions to
the target path, then a forced `--endpoint generate_content` run to exercise the
fallback path. The smoke-test script gains an opt-in Pro/4K check.

## Files touched

- `config/gemini-delegate.toml` — model-ID fix + `[image]` section
- `src/gemini_delegate/config.py` — `image_endpoint` accessor
- `src/gemini_delegate/image_backends.py` — **new**: request, backends, dispatcher
- `src/gemini_delegate/core.py` — `image()` becomes a thin wrapper over the seam
- `src/gemini_delegate/cli.py` — new `image` options
- `tests/` — new `test_image_backends.py`; updates to core/cli/config tests
- `CLAUDE.md` — amend §2.2/§13; note in §0
- `README.md` — document the new `image` options + endpoint config
- `scripts/smoke_test.py` — opt-in Pro/4K + fallback checks

## Risks / to-verify before building

1. **`Api-Revision: 2026-05-20` header** — confirm whether the SDK sets it
   automatically for `interactions` calls or it must be passed via
   `http_options`. Resolve before relying on it.
2. **Exact image-config signatures on BOTH paths** — pin the real parameter
   names in 2.9.0 against the installed SDK: for Interactions
   (`input` vs `contents`, the `response_format`/`ImageResponseFormat` shape, how
   `output_image` is exposed) and for generateContent (how `image_size`/
   `aspect_ratio` are passed — `GenerateContentConfig.image_config` vs a nested
   `response_format`, and whether `response_modalities` stays `["IMAGE"]`). Each
   backend encapsulates whatever it is, so the design is robust, but calls are
   written against verified signatures, not the doc summaries.
3. **`n>1` semantics** — confirm whether Interactions can return multiple images
   in one call; default to looping the call `n` times if not.

## Decisions (from brainstorm)

- Scope: **image now, structured to extend** (op-agnostic dispatcher seam).
- Endpoint control: **config default + CLI override**; path reported in warnings.
- Controls: **`--size` and `--aspect-ratio`** exposed now.
