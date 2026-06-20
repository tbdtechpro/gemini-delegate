# gemini-delegate — Project Charter & Build Instructions

> This file is **both** the build spec and the ongoing project memory for
> `gemini-delegate`. The architecture below is **decided**. Implement it as
> written — do not re-open the design or substitute a different integration
> pattern (no MCP server for now; see "Out of scope").

---

## 0. Current status (as-built — 2026-06-19)

**Built and live-verified.** All nine build-order milestones (§9) are complete.
The sections below remain the authoritative design reference; this section is the
quick orientation for a session picking the project up.

- **Implementation** lives in `src/gemini_delegate/` exactly as laid out in §3:
  `config.py` → `media.py`/`session.py` → `core.py` → `cli.py`. The layering in
  §6 holds: only `cli.py` knows the envelope and exit codes; only `config.py`
  knows model IDs.
- **Tests:** 57 offline unit tests, Gemini client mocked, no network.
  `make test` (or `python -m pytest`) runs them. Shared CLI fakes are in
  `tests/_helpers.py` / `tests/conftest.py`.
- **Live smoke test:** `scripts/smoke_test.py`, gated behind `RUN_LIVE=1` + a
  present `GEMINI_API_KEY`. Last run **4/4 pass** (describe, ask, image, video).
  The §12 model IDs and `response_modalities=["IMAGE"]` for image generation are
  confirmed against the live API as of this date.
- **Install:** `pipx install --editable .` puts `gemini-delegate` on PATH;
  `make install-agent` installs the subagent to `~/.claude/agents/`.
- **Dev environment:** a project `.venv` with `pip install -e ".[dev]"`. The CLI
  binary is installed separately via pipx (its own isolated venv).

**Working conventions for changing this project:**
- TDD (red → green), Gemini client always mocked in unit tests — no network in
  the default run (§10).
- Model IDs are config, never source (§2.4, §12); when they churn, edit
  `config/gemini-delegate.toml` only.
- Keep runtime deps to `google-genai`, `click`, `pillow` (§2.8).
- Verify SDK field names against the installed `google-genai` before relying on
  them — they shift between versions (§5, §6).

**Amendment (2026-06-20) — key resolution + ergonomics.** §2.3's "environment
only" is relaxed: the CLI resolves `GEMINI_API_KEY` from the environment, then
`$GEMINI_DELEGATE_ENV`, then `~/.config/gemini-delegate/.env`
(`core.resolve_api_key`). The key is still never accepted on the command line,
never logged, never written to a session file. This removes the ~25k-token
"hunt for the .env" flailing every subagent did. Also added `--prompt-file PATH`
(alternative to `--prompt`) so long/multi-line prompts stay out of the shell
command line — keeps Bash calls single-line so they match a
`Bash(gemini-delegate:*)` allowlist and don't trip newline-approval. The driving
subagent (`agents/gemini-delegate.md`) now tells agents the key is auto-resolved
(don't hunt for it), to run bare single-line commands, and to use `--prompt-file`
for long prompts.

**Known follow-ups** (tracked on the KeroAgile board): `--cleanup`/expiry has no
live test; `ask`/`describe`/`video` lack a structured-output live check; no
packaged JSON-schema validation of the `--schema` file itself; CI not wired.

**Amendment (2026-06-20) — Interactions image path.** The `image` op now routes
through the `interactions` surface by default (Beta); `generate_content` remains
the automatic fallback. Config: `[image].endpoint = auto|interactions|generate_content`.
See §2.2 amendment and `docs/superpowers/specs/2026-06-20-interactions-image-generation-design.md`.

---

## 1. What we're building

A general-purpose way for Claude Code to offload multimodal work it's weaker
at — **image-to-text, video-to-text, and text-to-image** — to the Google
Gemini API, usable across any project.

Two layers, strictly separated:

- **Mechanism** — a thin, deterministic Python CLI (`gemini-delegate`) that
  does nothing but talk to Gemini: build the request, transfer media, run the
  call, persist multi-turn state, and print a structured result. No judgment,
  no project awareness.
- **Policy** — a Claude Code **subagent** that drives the CLI: writes the
  prompt, validates and sanity-checks Gemini's output, structures it per
  project, follows up when needed, and returns only the clean artifact to the
  main session.

The CLI is a wrapper over a reusable core library, so an MCP shim over the same
core is a trivial future add — but not now.

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

---

## 2. Non-negotiable constraints

1. **SDK:** use `google-genai` (`from google import genai`). Do **not** use the
   legacy `google-generativeai` package.
2. **API path:** build on `client.models.generate_content(...)`. Do **not** use
   the newer `interactions` surface — stability over novelty for a tool.
   *Amended 2026-06-20: the `interactions` surface is now the **primary** path
   for the `image` op (Beta), with `generate_content` as automatic fallback
   (config key `[image].endpoint = auto|interactions|generate_content`). See
   `docs/superpowers/specs/2026-06-20-interactions-image-generation-design.md`.*
3. **API key:** read `GEMINI_API_KEY` from the environment only. Never accept it
   on the command line, never log it, never echo it, never write it to a session
   file.
4. **Model IDs are config, never code.** All model selection goes through the
   config's logical roles. No hardcoded model strings anywhere in the source.
5. **Output contract:** every command prints exactly one JSON envelope to stdout
   (schema in §5) and sets its exit code from `ok`. No bare tracebacks to stdout.
6. **Media:** inline-base64 only for single-shot small images; everything else
   (all video, large images, and **any** media in a multi-turn session) goes
   through the Files API and is referenced by URI. Cache uploads by content hash
   to avoid re-uploading across turns.
7. **Cost discipline:** default to the cheap Flash-tier models. Premium models
   (`image_pro`, `reason`) are opt-in only.
8. **Dependency minimalism:** runtime deps are `google-genai`, `click`, and
   `pillow` (for saving/inspecting generated images). Nothing else without a
   stated reason.
9. **Tests run offline.** Unit tests mock the Gemini client; no network in the
   default test run.

---

## 3. Repository layout

```
gemini-delegate/
├── CLAUDE.md                       # this file
├── pyproject.toml                  # pipx-installable; console_script gemini-delegate
├── README.md                       # short usage + install
├── Makefile                        # install / test / lint convenience targets
├── config/
│   └── gemini-delegate.toml        # packaged default config (model roles, paths)
├── src/gemini_delegate/
│   ├── __init__.py
│   ├── config.py                   # load TOML + env overrides, resolve model roles
│   ├── media.py                    # inline vs Files API, hash cache, cleanup
│   ├── session.py                  # session JSON read / append / write
│   ├── core.py                     # all Gemini logic; returns plain dicts
│   └── cli.py                      # Click commands; wraps core into the envelope
├── agents/
│   └── gemini-delegate.md          # the subagent (installed to ~/.claude/agents/)
├── examples/
│   └── project-gemini.md           # sample .claude/gemini.md for a consuming project
└── tests/
    ├── test_config.py
    ├── test_session.py
    ├── test_envelope.py
    └── test_cli.py
```

Python **≥ 3.11** (so `tomllib` is in the stdlib).

---

## 4. CLI contract — commands

Console script: `gemini-delegate`. Four subcommands map 1:1 to the supported
operations. Common options (`--json`, `--schema`, `--session`, `--model`) behave
identically across commands.

```
gemini-delegate describe <image...>     --prompt TEXT [--json] [--schema PATH]
                                         [--session PATH] [--model ROLE|ID]

gemini-delegate video    <file|url>      --prompt TEXT [--json] [--schema PATH]
                                         [--session PATH] [--model ROLE|ID]

gemini-delegate image    --prompt TEXT   --out PATH [--ref PATH ...]
                                         [--model ROLE|ID] [--n INT]

gemini-delegate ask      --prompt TEXT   [--session PATH] [--json] [--schema PATH]
                                         [--model ROLE|ID]
```

Option semantics:

- `--prompt TEXT` — required everywhere. The instruction Claude generated.
- `--model ROLE|ID` — a logical role (`text`, `vision`, `video`, `image`,
  `image_pro`, `reason`) resolved via config, **or** an explicit model ID for
  escape hatches. Defaults: `describe`→`vision`, `video`→`video`,
  `image`→`image`, `ask`→`text`.
- `--json` — request structured JSON output (sets Gemini's JSON response mode).
- `--schema PATH` — a JSON Schema file; implies `--json`; passed as the response
  schema so structure is enforced at the API boundary, then validated on return.
- `--session PATH` — enables multi-turn. Reads prior turns from the file, sends
  the new turn, appends the response, writes the file back. Absent → single-shot.
- `describe` accepts one or more image paths (positional).
- `video` accepts a local file path **or** a URL (e.g. a YouTube link, passed
  through directly per the Files API rules in §6).
- `image` writes the generated image to `--out` and may take repeatable `--ref`
  reference images for editing/consistency; `--n` defaults to 1.

---

## 5. CLI contract — the output envelope

**This is load-bearing.** The subagent parses this and nothing else. Print
exactly one JSON object to stdout per invocation.

```json
{
  "ok": true,
  "op": "describe",
  "model": "gemini-3.5-flash",
  "text": "…model's text output, or null…",
  "json": { "…": "…" },
  "files": ["/abs/path/out.png"],
  "session": "/abs/path/session.json",
  "usage": { "input_tokens": 0, "output_tokens": 0 },
  "warnings": [],
  "error": null
}
```

Rules:
- `ok` is `true` only on a clean success. Exit code: `0` when `ok` is true, `1`
  on any failure, `2` on a usage/argument error.
- `text` is the model's text (or `null` for pure image generation).
- `json` is the parsed object when `--json`/`--schema` was used and parsing
  succeeded; otherwise `null`. If JSON parsing fails, set `ok=false` with an
  `error`, do **not** silently fall back to `text`.
- `files` lists absolute paths to anything written (generated images).
- `session` is the absolute session path when `--session` was used, else `null`.
- `usage` maps Gemini's `usage_metadata` to input/output token counts. **Verify
  the exact field names against the installed SDK** (`prompt_token_count`,
  `candidates_token_count`, etc.) — they have shifted between versions.
- `error`, when present, is `{ "type": "...", "message": "..." }`. Catch all
  exceptions at the CLI boundary, populate this, exit non-zero. Never let a
  traceback reach stdout (stderr is fine for `--debug`). Never include the key.

---

## 6. Core library requirements (`core.py`, `media.py`, `session.py`)

**Client init.** Construct one `genai.Client()` reading the key from env. Fail
fast with a clean error envelope if the key is missing.

**Model resolution** (`config.py`). Resolve a role to an ID via config; if the
caller passed something that isn't a known role, treat it as an explicit ID.

**Media handling** (`media.py`):
- Single-shot + small image + no session → inline base64 part.
- All video, large images, and **any** media in a `--session` → upload via
  `client.files.upload(...)` and reference the returned URI/`Part.from_uri`. This
  keeps multi-turn cheap (reference, not re-send) and keeps the session file
  small.
- Cache uploads in a local map keyed by `sha256(file_bytes)` → `{uri, mime,
  name, expires}` so the same file isn't uploaded twice within a session.
- For `video` with a URL, pass it through as file-data without uploading.
- Files API objects expire on Google's side (~48h); rely on that for cleanup,
  and expose an optional `--cleanup` to delete session files explicitly. Note in
  output `warnings` if a referenced upload looks expired.

**The four operations** (`core.py`), each returning a plain dict the CLI wraps:
- `describe` — vision model, image part(s) + prompt → text/json.
- `video` — video model, uploaded video (or URL) + prompt → text/json.
- `image` — image model, prompt (+ optional refs) → write `inline_data` parts to
  disk (`part.as_image().save(...)`), return file paths.
- `ask` — text model, prompt only (plus session history if any) → text/json.

**Structured output.** When `--json`/`--schema` is set, pass
`response_mime_type="application/json"` (and `response_schema` when a schema is
given) via the generation config. Parse the returned text as JSON; populate
`json`; on failure set `ok=false`.

**Sessions** (`session.py`). A session is JSON holding the model role and the
running `contents` list (`[{role, parts}]`), plus the upload cache. Each call:
load → append the new user turn → send the full `contents` → append the model
turn → write back. `generate_content` is stateless, so the `contents` list *is*
the memory.

---

## 7. The subagent (`agents/gemini-delegate.md`)

Installed to `~/.claude/agents/gemini-delegate.md` (user scope → every project).

Frontmatter:

```yaml
---
name: gemini-delegate
description: >
  Delegates multimodal work to the Gemini API — image-to-text, video-to-text,
  and text-to-image. Use PROACTIVELY whenever a task needs image or video
  understanding, or image generation, that Gemini handles better than Claude.
tools: Bash, Read, Write
model: sonnet
---
```

The body must instruct the subagent to:

1. **Never call Gemini directly.** Its only interface is the `gemini-delegate`
   CLI via Bash. No API logic, no model IDs in the prompt.
2. **Read project conventions first.** Check `./.claude/gemini.md` and the
   project `CLAUDE.md` for the desired output schema/format, and prefer the CLI's
   `--schema` so structure is enforced at the API boundary.
3. **Map the task to one of the four subcommands** and build a precise prompt.
4. **Parse the JSON envelope** from stdout; honor `ok` and the exit code. Never
   parse prose.
5. **Validate per flow before returning:**
   - `describe`/`video`: output isn't a refusal ("I can't see the image"), isn't
     generic boilerplate, and matches the requested schema when one was given.
   - `image`: a file exists at the expected path, is non-trivially sized, and the
     correct format. Then **`Read` the generated image** (the subagent is itself
     multimodal) and confirm it matches the request.
   - structured: the `json` field parsed and required fields are present and
     sanely typed.
6. **Follow up in-session when confidence is low** — send a clarifying `ask` or
   re-run on the same `--session` before returning, rather than passing a shaky
   answer upstream.
7. **Return only the clean artifact** to the main session: file paths, parsed
   JSON, and a one-to-two-line summary. Never dump raw transcripts or base64.
   Surface the model used and token usage when cost is relevant.
8. **Default to cheap models.** Reach for `image_pro` or `reason` only when the
   task explicitly needs the fidelity, or the user asked.

Note: custom-subagent auto-routing is unreliable — Claude often handles the task
in the main session instead of delegating. The "use PROACTIVELY" wording helps;
explicit invocation ("use the gemini-delegate subagent to…") is the reliable
trigger.

---

## 8. Project structuring hook

A consuming project may drop a `./.claude/gemini.md` describing its desired
output shape (e.g. the JSON schema for extracted fields, naming conventions,
where generated assets go). The subagent reads it and the project `CLAUDE.md`;
no code changes are needed to onboard a new project. Ship `examples/project-gemini.md`
as a template.

---

## 9. Build order (checkpoint after each milestone)

1. **Scaffold:** repo layout, `pyproject.toml` (console script, deps),
   `Makefile`, packaged default `config/gemini-delegate.toml`, README stub.
2. **Config:** loader with resolution order `$GEMINI_DELEGATE_CONFIG` →
   `~/.config/gemini-delegate/config.toml` → packaged default; role→ID
   resolution; env overrides. Tests.
3. **Media + session:** inline vs Files API logic, hash cache, session
   read/append/write. Tests (mocked client).
4. **Core ops:** `describe`, `video`, `image`, `ask`, including structured-output
   mode. Tests (mocked client).
5. **CLI:** Click commands, the JSON envelope, exit codes, error boundary,
   `--debug`. Tests via Click's runner, asserting envelope schema and exit codes.
6. **Install + verify:** `pipx install --editable .`; confirm `gemini-delegate
   --help` and each subcommand parse on PATH.
7. **Subagent:** write `agents/gemini-delegate.md` per §7; install to
   `~/.claude/agents/`; document the install in README and Makefile.
8. **Project hook:** `examples/project-gemini.md` + README section.
9. **Live smoke test (I run this):** with a real `GEMINI_API_KEY`, one call per
   subcommand. You scaffold the smoke-test script; I execute it.

---

## 10. Testing

- Mock `genai.Client` (monkeypatch) so unit tests need no key and no network.
- Assert: envelope shape and field types, exit codes (`0/1/2`), session
  append/replay round-trips, structured-output parse-failure → `ok=false`,
  missing-key → clean error envelope.
- A live smoke test is gated behind `RUN_LIVE=1` **and** a present
  `GEMINI_API_KEY`; it is never part of the default `make test`.

---

## 11. Coding conventions

- Type-hinted throughout; small pure functions in `core`/`media`/`session`.
- `core` returns plain dicts; only `cli` knows about the envelope and exit codes.
- Format/lint with `ruff` if available; not load-bearing.
- Keep the dependency surface to the three runtime deps in §2.

---

## 12. Model IDs — current as of June 2026

Put these in `config/gemini-delegate.toml`. **Verify against Google's docs (or
`client.models.list()`) and update the config when they move — they churn fast.
Do not hardcode them in source.**

```toml
[models]
text      = "gemini-3.5-flash"            # plain text / follow-ups
vision    = "gemini-3.5-flash"            # image + doc -> text
video     = "gemini-3.5-flash"            # video -> text (samples ~1 fps)
reason    = "gemini-3.1-pro-preview"      # hard structured extraction
image     = "gemini-3.1-flash-image"      # Nano Banana 2, cheap default
image_pro = "gemini-3-pro-image"          # Nano Banana Pro: ~$0.134/img, 4K, best text; opt-in
```

Notes: generated images carry a SynthID watermark. Nano Banana Pro has no free
tier. Video can also be supplied as a YouTube URL (free-tier daily limits apply).

---

## 13. Out of scope (for now — do not build)

- An MCP server. The core lib is structured so a shim is easy later; building it
  now is wasted effort.
- Imagen / Veo, batch processing, ~~the `interactions` API~~ *(amended
  2026-06-20: `interactions` is now the primary endpoint for the `image` op —
  see §2.2 amendment and
  `docs/superpowers/specs/2026-06-20-interactions-image-generation-design.md`)*.

If a task seems to need one of the remaining items above, stop and ask rather than expanding scope.

---

## 14. Definition of done

- [ ] `pipx install --editable .` puts `gemini-delegate` on PATH; `--help` works.
- [ ] All four subcommands parse and produce a valid envelope on mocked calls.
- [ ] Exit codes match the contract; the API key never appears in output.
- [ ] Multi-turn sessions persist and replay correctly.
- [ ] `--schema` enforces and validates structured output.
- [ ] `~/.claude/agents/gemini-delegate.md` installed; subagent invokes the CLI,
      validates, and returns a clean artifact.
- [ ] `examples/project-gemini.md` documented.
- [ ] `make test` passes offline; the live smoke test is ready for me to run.
