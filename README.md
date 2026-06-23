<p align="center">
  <img src="assets/logo.png" alt="gemini-delegate" width="260">
</p>

<h1 align="center">gemini-delegate</h1>

<p align="center">
  <em>Give Claude Code a Gemini sidekick — image, video, and image-generation
  skills it doesn't have natively, one shell command away.</em>
</p>

`gemini-delegate` is a small, deterministic CLI (plus a Claude Code subagent that
drives it) for handing the **multimodal work Claude is weaker at** —
**image→text, video→text, and text→image** — to the Google Gemini API, plus
**grounded web search** for the long tail. It works in any project, prints one
clean JSON envelope per call, and never puts your API key on the command line.

It's built as two strictly separated layers:

- **Mechanism** — the `gemini-delegate` CLI. It only talks to Gemini: build the
  request, move the media, run the call, persist multi-turn state, print one
  JSON envelope. No judgment, no project awareness.
- **Policy** — a Claude Code **subagent** that drives the CLI: it writes the
  prompt, validates Gemini's output (it'll even *look at* a generated image,
  since it's multimodal too), shapes results to your project, retries when
  unsure, and hands back only the clean artifact.

```
main Claude Code session
        │  delegates a multimodal task
        ▼
gemini-delegate subagent  (policy: prompt, validate, structure, follow up)
        │  shell calls
        ▼
gemini-delegate CLI  ──►  core lib  ──►  Gemini API
        │
        ▼
JSON envelope on stdout  ──►  subagent validates  ──►  clean result to main session
```

> **Status:** built and live-verified — **122 offline unit tests** (the Gemini
> client is mocked; no network, no key) plus a gated live smoke test across every
> subcommand.

## What it can do

| Capability | What it does | vs Claude Code | vs direct Python library | vs best free local model (RTX 3090) |
|---|---|---|---|---|
| **Image → text** · `describe` | Read, OCR, and analyze images | Some improvement *(big on dense OCR / fine technical detail)* | Significant improvement · [pytesseract](https://github.com/madmaze/pytesseract) *(OCR only)* | Significant improvement · [Qwen2.5‑VL‑7B](https://hf.co/Qwen/Qwen2.5-VL-7B-Instruct) |
| **Video → text** · `video` | Answer questions about a video file or YouTube URL | ❌ · no comparable option | ❌ · no comparable option | Significant improvement · [Qwen2.5‑VL‑7B](https://hf.co/Qwen/Qwen2.5-VL-7B-Instruct) |
| **Text → image** · `image` | Generate / edit images (Nano Banana), incl. transparent PNGs | ❌ · no comparable option | ❌ · no comparable option | Significant improvement *(in-image text, editing)* · [FLUX.1‑schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell) |
| **Text / reasoning** · `ask` | Q&A, structured extraction, multi-turn follow-ups | Worse | ❌ · no comparable option | Equivalent · [Qwen2.5‑32B](https://huggingface.co/bartowski/Qwen2.5-32B-Instruct-GGUF) |
| **Web search** · `search` | Grounded Google web search → answer + source URLs | Some improvement *(niche / non-English / very recent)* | ❌ · no comparable option | ❌ · no comparable option |

Each rating describes how the **Gemini** integration compares to that alternative
(*Worse / Equivalent / Some improvement / Significant improvement*; **❌** = no
comparable option). The **Python** column looks for a library that does the task
*directly* — a classical/algorithmic approach, **not** a wrapper around the
neural model in the next column — so it's **❌** for anything that fundamentally
needs a model (video understanding, image generation, general reasoning). The
**local-model** column targets a single **NVIDIA RTX 3090 (24 GB VRAM)**. This is
a rough, hand-made guide — **not** benchmarks — and there are surely capable
libraries or models our search didn't surface. (More capabilities — speech, audio
understanding, music, embeddings — are on the
[integration roadmap](docs/integration-roadmap.md).)

## Quick start

**1. Get a Gemini API key.** Grab a free one from
[Google AI Studio](https://aistudio.google.com/apikey) — sign in with a Google
account and click **Create API key**. Drop it in the key file (read once, never
logged, never passed on the command line):

```sh
mkdir -p ~/.config/gemini-delegate
printf 'GEMINI_API_KEY=%s\n' "YOUR_KEY_HERE" > ~/.config/gemini-delegate/.env
chmod 600 ~/.config/gemini-delegate/.env
```

(Or just `export GEMINI_API_KEY=…` in your shell — the CLI checks the environment
first, then `$GEMINI_DELEGATE_ENV`, then that key file.)

**2. Install the CLI** (Python ≥ 3.11):

```sh
pipx install --editable .          # or: make install-editable
gemini-delegate --help
```

**3. Install the driving subagent** (user scope → available in every project):

```sh
make install-agent                 # copies agents/gemini-delegate.md to ~/.claude/agents/
```

**4. Try it:**

```sh
gemini-delegate describe photo.jpg --prompt "What's in this image? One sentence."
gemini-delegate image --transparent --prompt "a small orange mascot, pixel art" --out mascot.png
```

From inside Claude Code, just ask: *"use the gemini-delegate subagent to describe
these screenshots and pull out the error text as JSON."*

## Commands

```
gemini-delegate describe <image...>  --prompt TEXT [--json] [--schema PATH] [--session PATH] [--model ROLE|ID]
gemini-delegate video    <file|url>  --prompt TEXT [--json] [--schema PATH] [--session PATH] [--model ROLE|ID]
gemini-delegate image    --prompt TEXT --out PATH [--ref PATH ...] [--n INT] [--model ROLE|ID]
                         [--transparent] [--chroma-key COLOR] [--chroma-tolerance INT] [--keep-original]
gemini-delegate ask      --prompt TEXT [--session PATH] [--json] [--schema PATH] [--model ROLE|ID]
gemini-delegate search   --prompt TEXT [--prompt-file PATH] [--model ROLE|ID]
```

| Option | Meaning |
|---|---|
| `--prompt TEXT` | The instruction (one of `--prompt`/`--prompt-file` required everywhere). |
| `--prompt-file PATH` | Read the prompt from a file — handy for long/multi-line prompts (cleaner shell, fewer approval prompts). |
| `--json` | Request structured JSON (sets Gemini's JSON response mode). |
| `--schema PATH` | A JSON Schema file; implies `--json`; enforced at the API boundary. |
| `--session PATH` | Multi-turn: prior turns are read, the new turn sent, the response appended. |
| `--model ROLE\|ID` | A logical role (`text`, `vision`, `video`, `image`, `image_pro`, `reason`) or an explicit model ID. |
| `--size SIZE` | (`image` only) Output resolution — one of `512`, `1K`, `2K`, `4K`. |
| `--aspect-ratio RATIO` | (`image` only) Aspect ratio hint: `1:1`, `16:9`, `4:3`, etc. |
| `--endpoint VALUE` | (`image` only) Force the generation backend: `auto` (default), `interactions`, or `generate_content`. |
| `--transparent` | (`image` only) Generate on a flat key color and chroma-key it to a transparent PNG/WebP. |
| `--chroma-key COLOR` | (`image` only) The flat color to remove (e.g. `#FF00FF` or `magenta`). Default: `#FF00FF`. Implies keying without `--transparent` when used alone. |
| `--chroma-tolerance INT` | (`image` only) Per-channel tolerance for the chroma-key (0–255). Default: `60`. |
| `--keep-original` | (`image` only) Also save the un-keyed original beside the output as `<stem>.orig.jpg`. |
| `--cleanup` | (session commands) Delete this session's uploaded Files API objects. |
| `--debug` | Print a traceback to **stderr** on failure (stdout stays clean JSON). |

### Image endpoint (Interactions vs generateContent)

The `image` subcommand has two generation backends, picked via `--endpoint` or
`[image].endpoint` in config:

| Value | Behaviour |
|---|---|
| `auto` *(default)* | Try the `interactions` surface first; fall back to `generate_content` automatically if it fails. |
| `interactions` | Force the Interactions API (Beta). Best quality and 4K support; no free tier for `image_pro`. |
| `generate_content` | Force the classic `generate_content` path. |

Both paths produce the same JSON envelope; a `warnings` entry is added when an
automatic fallback happens, so you always know which backend ran.

```sh
# Pro model, 4K, forced Interactions path
gemini-delegate image --model image_pro --endpoint interactions \
  --size 4K --aspect-ratio 16:9 \
  --prompt "A red maple leaf on white, studio lighting" --out leaf.png
```

### Transparent images

`--transparent` generates on a flat magenta key color (`#FF00FF`) and chroma-keys
it out to an alpha channel, so you get a PNG/WebP with true transparency. A few
things to know:

- **Don't put a background in your prompt.** Let the model fill the field with
  the key color; asking for a specific background fights the keying step.
- **Output must be `.png` or `.webp`.** JPEG can't store alpha — a `.jpg`/`.jpeg`
  `--out` is rejected with a usage error.
- **JPEG-source caveat.** Gemini returns JPEG internally, so hard edges can pick
  up faint fringing after keying. Tune `--chroma-tolerance`, or use
  `--keep-original` to grab the un-keyed image and clean up by hand.

```sh
gemini-delegate image --transparent --prompt "a small orange creature" --out creature.png
gemini-delegate image --transparent --chroma-tolerance 80 --keep-original --prompt "a small orange creature" --out creature.png
```

> Fun fact: this project's logo was generated by `gemini-delegate` itself via the
> `--transparent` path.

### Grounded web search

`search` runs a Google-grounded web query through Gemini and returns the answer
plus the **source URLs** it grounded on. Claude Code already has a solid native
web search, so reach for this on the long tail — niche, non-English, or
very-recent topics where the native search comes up short (e.g. hunting down
Japanese hardware/emulation forum threads).

```sh
gemini-delegate search --prompt "Sega Super Prologue 21 SKC-3000 service manual or teardown"
```

`text` holds the grounded answer; `json` holds `{ sources: [{uri, title, domain}], queries: [...] }` so you can go straight to the source links. A `warnings` entry appears if the model answered without actually searching.

> Grounding can run slow. Every call uses a finite client-side timeout (default
> **120s**, set `[client].timeout_seconds` or `GEMINI_DELEGATE_TIMEOUT=<seconds>`;
> `0` disables) so a stalled request fails with `error.type: "timeout"` rather
> than hanging forever — raise it if a legitimately heavy search needs longer.

## The output envelope

Every invocation prints **exactly one JSON object** to stdout and sets its exit
code from the result: `0` ok, `1` failure, `2` usage/argument error.

```jsonc
{
  "ok": true,
  "op": "describe",
  "model": "gemini-3.5-flash",
  "text": "A red square.",
  "json": null,                 // populated when --json/--schema is used
  "files": [],                  // absolute paths to anything written
  "session": null,              // absolute session path when --session is used
  "usage": { "input_tokens": 1104, "output_tokens": 11 },
  "warnings": [],
  "error": null                 // { "type": "...", "message": "..." } on failure
}
```

On a JSON-parse failure the tool sets `ok:false` with an `error` — it never
silently falls back to `text`.

```sh
$ gemini-delegate ask --prompt "Reply with exactly one word: pong"
{"ok": true, "op": "ask", "model": "gemini-3.5-flash", "text": "pong", "json": null,
 "files": [], "session": null, "usage": {"input_tokens": 8, "output_tokens": 1},
 "warnings": [], "error": null}
```

## Configuration

Model selection goes through logical **roles** resolved from TOML — model IDs are
config, never code, so when Google moves a model you edit one file, not source.
Resolution order: `$GEMINI_DELEGATE_CONFIG` →
`~/.config/gemini-delegate/config.toml` → packaged default
([`config/gemini-delegate.toml`](config/gemini-delegate.toml)). A single role can
also be pinned via `GEMINI_DELEGATE_MODEL_<ROLE>` for a one-off run. Costs default
to the cheap Flash-tier roles; `image_pro` (Nano Banana Pro) and `reason` are
opt-in.

## Onboarding a project (the project hook)

A consuming project can steer the subagent's output **without any code change**:
drop a `./.claude/gemini.md` describing the desired output shape (e.g. a JSON
Schema for extracted fields), naming conventions, and where generated assets go.
The subagent reads it — along with the project `CLAUDE.md` — before every
delegation.

```sh
mkdir -p .claude
cp examples/project-gemini.md .claude/gemini.md   # then edit for your project
```

See [`examples/project-gemini.md`](examples/project-gemini.md) for a worked
example (schema, asset naming, model/cost policy, validation expectations).

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
make test          # 122 offline unit tests; the Gemini client is mocked (no network, no key)
```

The live smoke test is gated behind `RUN_LIVE=1` **and** a present
`GEMINI_API_KEY`; it is never part of `make test`:

```sh
RUN_LIVE=1 python scripts/smoke_test.py     # one real call per subcommand
```

## Layout

```
config/gemini-delegate.toml   packaged default config (model roles, paths)
src/gemini_delegate/
  config.py          load TOML + env overrides, resolve model roles
  media.py           inline vs Files API, sha256 upload cache, cleanup
  session.py         session JSON read / append / write
  core.py            all Gemini logic; returns plain dicts
  image_backends.py  Interactions + generateContent image backends, endpoint dispatch
  imaging.py         transcode + chroma-key transparency (Pillow only)
  cli.py             Click commands; wraps core into the JSON envelope
agents/gemini-delegate.md     the subagent (installed to ~/.claude/agents/)
examples/project-gemini.md    sample .claude/gemini.md for a consuming project
docs/integration-roadmap.md   planned capabilities (speech, audio, music, embeddings)
scripts/smoke_test.py         gated live smoke test
tests/                        offline unit tests (mocked client)
```

## License

MIT
