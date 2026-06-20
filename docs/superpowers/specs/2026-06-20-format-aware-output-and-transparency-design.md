# Design: format-aware output + transparency (chroma-key)

**Date:** 2026-06-20
**Status:** Approved (brainstorm), pending spec review → implementation plan
**Repo:** gemini-delegate (`/home/matt/github/GemCLI-Helper`)
**Branch:** continues on `feat/interactions-image-gen` (depends on the image backends / `core.image` from that feature; merge is held pending another active session).

## Summary

Two cohesive post-generation output capabilities for the `image` op:

1. **Transcode-on-save** — save the generated image in the format implied by the
   `--out` extension (png/jpg/webp), transcoding the JPEG the API returns, so a
   `.png` request yields a real PNG instead of JPEG bytes in a `.png` file.
2. **Transparency** — a `--transparent` flag (sugar) that injects a flat
   key-color background directive into the prompt, requires an alpha-capable
   `--out` (PNG/WebP), then chroma-keys that color to alpha and validates; built
   over a lower-level `--chroma-key COLOR` primitive. Uses **Pillow** (already a
   runtime dep) — no new dependency.

## Motivation & grounding (verified live 2026-06-20)

- Gemini image generation returns **JPEG (RGB, no alpha)** on **both** endpoints
  (`interactions` and `generate_content`) and **both** models
  (`gemini-3.1-flash-image`, `gemini-3-pro-image`). There is no lossless/PNG/alpha
  source.
- Consequences this design accepts:
  - "Format-aware *endpoint* routing" is moot (both paths are JPEG); the real need
    is format-aware **saving** (transcode). Endpoint stays a quality/feature
    choice. (Brainstorm decision: "transcode-on-save only.")
  - The model cannot emit true transparency; when asked for a "transparent
    background" it *drew a checkerboard*. So transparency must be produced by
    generating on a **flat key color** and removing it in post.
  - Keying operates on JPEG pixels, so **edge fringing around the key color is
    inherent**; mitigated by a tolerance band (a defringe/erode pass is a
    documented future enhancement, not v1).
- `rembg` (AI matting) was considered and rejected: heavy dependency
  (onnxruntime + ~170MB model), soft anti-aliased mattes that ruin hard pixel
  edges, photo-oriented. Pillow chroma-key is the right tool here and adds no dep.

## Scope

**In scope (image op only):** transcode-on-save; `--transparent` /
`--chroma-key COLOR` / `--chroma-tolerance INT` / `--keep-original`; keying +
validation-as-warnings; usage-error guards.

**Out of scope (now):** defringe/erode edge cleanup; pixel-grid snapping;
`rembg`/AI matting; transparency for any op other than `image`; native-PNG/alpha
from the API (doesn't exist).

## Architecture

New pure module **`src/gemini_delegate/imaging.py`** — PIL only, no SDK, fully
offline-testable. Functions (names are the contract). All work on PIL `Image`
objects to avoid double-encoding; only the final `save_image` touches disk:

- `decode(data: bytes) -> Image` — decode API bytes to a PIL image; raises
  `ImagingError` on undecodable bytes.
- `save_image(img: Image, out_path: str) -> str` — save in the format implied by
  `out_path`'s extension (png/jpg/webp/…); returns the absolute path written.
- `chroma_key(img: Image, key_rgb: tuple[int,int,int], tolerance: int) -> tuple[Image, dict]`
  — return an RGBA image with `alpha=0` where RGB-distance to `key_rgb` ≤
  tolerance, plus stats `{removed_fraction: float, corners_transparent: bool}`.
- `validate_key(stats: dict) -> list[str]` — heuristic warnings (see §Validation).
- `parse_color(s: str) -> tuple[int,int,int]` — accept `#RRGGBB`, `RRGGBB`, and a
  small set of names (`magenta`, `green`, `cyan`, `white`, `black`); raise
  `ImagingError` on a bad string (CLI maps to a usage error).

Backends are unchanged (return raw `bytes`). `core.image` orchestrates: resolve
options → (if transparent) append the prompt directive + require an alpha-capable
`--out` → run backend → for each returned image: `decode`, then transcode-save
(`save_image`) **or** `chroma_key`+`validate_key`+`save_image` (and optionally
write the original) → collect warnings → return the result dict.

`core` keeps owning the result dict and file paths; `imaging` owns only
"bytes → processed bytes/file + stats". `cli` owns flags and the envelope.

## Component: transcode-on-save

`core.image` replaces the current raw-bytes writer (`_save_image_bytes`) with a
call to `imaging.save_bytes_as(data, path)` per generated image (numbered for
`n>1` exactly as today: `out`, then `out_2`, `out_3`, …). Result: the file is a
valid image in the `--out` extension's format. Non-`--transparent` path only
transcodes (no alpha).

## Component: transparency

- **Default key color:** magenta `#FF00FF` (rare in typical subjects; classic
  sprite key).
- **Prompt directive (deterministic), appended by `core.image` when
  transparent:** *"Render the entire subject on a solid, flat, uniform pure
  magenta (#FF00FF) background — one single flat fill color, with no other
  background elements, no checkerboard or transparency pattern, no gradient, no
  texture, and no shadow cast on the background."* (Color text matches the
  resolved key color when `--chroma-key` overrides the default.)
- **`--transparent`** (flag): default-magenta directive + require alpha-capable
  `--out` (PNG/WebP) + chroma-key magenta + validate.
- **`--chroma-key COLOR`**: the primitive — key out `COLOR` (no prompt
  injection). When combined with `--transparent`, `COLOR` drives both the
  directive and the key.
- **`--chroma-tolerance INT`** (default 60): RGB-distance threshold; absorbs JPEG
  fringe around the key color.
- **`--keep-original`** (flag): also write the un-keyed source beside the output
  as `<stem>.orig.jpg` (source format), for manual cleanup in an external editor.

## Component: CLI surface

`image` command gains: `--transparent` (flag), `--chroma-key COLOR` (str),
`--chroma-tolerance INT` (default 60), `--keep-original` (flag). All other
options unchanged. `core.image(...)` gains matching keyword params
(`transparent: bool`, `chroma_key: str|None`, `chroma_tolerance: int`,
`keep_original: bool`).

## Validation → warnings (never silent)

After keying, `validate_key(stats)` appends to the envelope `warnings` (the image
is still written; this is never a hard error):

- `removed_fraction < 0.05` → "background may not have keyed cleanly (only N% of
  pixels removed) — check the key color / regenerate".
- `removed_fraction > 0.95` → "almost the entire image was removed — the subject
  may share the key color".
- corners not all transparent → "image corners did not key out — background may
  not be a clean flat fill".

The policy layer (subagent) reads these and decides whether to retry. A hard
error (`ok:false`) occurs only when **no image was generated at all**
(`no_image`, as today) or bytes are undecodable.

## Error handling / edge cases

- `--transparent` or `--chroma-key` with a non-alpha `--out` extension
  (`.jpg`/`.jpeg`) → **`click.UsageError`, exit 2** (JPEG can't hold alpha).
  Alpha-capable outputs: `.png`, `.webp`.
- Invalid `--chroma-key` color string → usage error (exit 2).
- Undecodable / corrupt API bytes → `CoreError` (exit 1, clean envelope).
- Documented: with `--transparent`, the user should not also specify a background
  in their own prompt (the directive handles it; conflicting wording confuses the
  model).
- `--keep-original` without transparency is a no-op (nothing to keep vs the saved
  file); treated as harmless (no original written) — documented.

## Testing (offline; mocked client + real Pillow)

`tests/test_imaging.py` (new):
- `save_bytes_as`: JPEG bytes → `.png` file is `format=="PNG"`; `.webp` → WEBP;
  `.jpg` → JPEG. Undecodable bytes → `ImagingError`.
- `chroma_key`: synthetic image (Pillow-drawn magenta field + a solid non-magenta
  square) → keyed result has the square opaque, the field transparent,
  `removed_fraction` in the expected band, `corners_transparent True`.
- tolerance: a near-magenta (JPEG-ish) pixel within tolerance keys out; outside
  tolerance stays.
- `validate_key`: each of the three warning triggers fires on crafted stats.
- `parse_color`: `#FF00FF`, `FF00FF`, `magenta` → `(255,0,255)`; bad string →
  `ImagingError`.

`tests/test_core.py` (extend):
- `--transparent` appends the magenta directive to the prompt sent to the backend
  and saves RGBA in the alpha-capable `--out` format; output file is an RGBA PNG.
- questionable keying (synthetic backend bytes that key to ~0% removed) →
  envelope `warnings` contains the low-removal message; `ok` stays true.
- `--keep-original` writes both `out.png` and `out.orig.jpg`.
- `--chroma-key "#00FF00"` keys green and does NOT append the magenta directive
  (assert the sent prompt is unmodified).
- `transparent=True` with `out=".jpg"` → `CoreError`/usage error path.

`tests/test_cli.py` (extend): the new flags parse; `image --transparent --out
x.jpg` → exit 2; `--chroma-tolerance` passes through.

`scripts/smoke_test.py`: an opt-in (e.g. `SMOKE_TRANSPARENT=1`) live check that
generates a simple subject `--transparent` and asserts the output PNG is RGBA
with a plausible transparent fraction.

## Files touched

- `src/gemini_delegate/imaging.py` — **new** (PIL post-processing)
- `src/gemini_delegate/core.py` — `image()` orchestration: directive injection,
  transcode/key on save, warnings; replace `_save_image_bytes` with `imaging`
- `src/gemini_delegate/cli.py` — new `image` flags
- `tests/test_imaging.py` — **new**; `tests/test_core.py`, `tests/test_cli.py` — extend
- `scripts/smoke_test.py` — opt-in transparency check
- `README.md` — document the new flags + transparency workflow + the JPEG-source
  caveat
- `config/gemini-delegate.toml` (optional) — a `[image]` default for key color /
  tolerance if we want config defaults; **decision: not in v1** (flags only) to
  keep scope tight

## Decisions (from brainstorm)

- Format work = **transcode-on-save only** (endpoint routing dropped; JPEG-only).
- Transparency = **inject key color + key it out**, `--transparent` sugar over a
  `--chroma-key COLOR` primitive (Pillow).
- Keying failure = **best-effort PNG + warnings**, plus an opt-in
  **`--keep-original`** raw copy. Hard error only when no image at all.
- Default key color **magenta `#FF00FF`**; default tolerance **60**.
- **Defringe/erode deferred** (documented future enhancement), not v1.
