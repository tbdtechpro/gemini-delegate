# gemini-delegate

A general-purpose way for **Claude Code** to offload the multimodal work it's
weaker at — **image-to-text, video-to-text, and text-to-image** — to the Google
Gemini API, usable across any project.

It's two strictly separated layers:

- **Mechanism** — a thin, deterministic Python CLI (`gemini-delegate`) that does
  nothing but talk to Gemini: build the request, transfer media, run the call,
  persist multi-turn state, and print one structured JSON envelope. No judgment,
  no project awareness.
- **Policy** — a Claude Code **subagent** that drives the CLI: it writes the
  prompt, validates Gemini's output (including *reading* generated images, since
  it's itself multimodal), structures results per project, follows up when
  confidence is low, and returns only a clean artifact to the main session.

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

> Status: **built and live-verified** — 57 offline unit tests (mocked client)
> plus a live smoke test passing 4/4 across all subcommands.

## Requirements

- Python ≥ 3.11 (uses stdlib `tomllib`)
- A `GEMINI_API_KEY`, resolved (in order) from: the environment, then
  `$GEMINI_DELEGATE_ENV`, then `~/.config/gemini-delegate/.env`. It is never
  passed on the command line, never logged, never written to a session file.
  The key file means every session and subagent gets the key with zero
  discovery:

  ```sh
  mkdir -p ~/.config/gemini-delegate
  printf 'GEMINI_API_KEY=%s\n' "$YOUR_KEY" > ~/.config/gemini-delegate/.env
  chmod 600 ~/.config/gemini-delegate/.env
  ```

## Install

```sh
pipx install --editable .          # or: make install-editable
gemini-delegate --help
```

Install the driving subagent (user scope → available in every project):

```sh
make install-agent                 # copies agents/gemini-delegate.md to ~/.claude/agents/
```

## Commands

```
gemini-delegate describe <image...>  --prompt TEXT [--json] [--schema PATH] [--session PATH] [--model ROLE|ID]
gemini-delegate video    <file|url>  --prompt TEXT [--json] [--schema PATH] [--session PATH] [--model ROLE|ID]
gemini-delegate image    --prompt TEXT --out PATH [--ref PATH ...] [--n INT] [--model ROLE|ID]
gemini-delegate ask      --prompt TEXT [--session PATH] [--json] [--schema PATH] [--model ROLE|ID]
```

| Option | Meaning |
|---|---|
| `--prompt TEXT` | The instruction (one of `--prompt`/`--prompt-file` required everywhere). |
| `--prompt-file PATH` | Read the prompt from a file — use for long/multi-line prompts (cleaner shell, fewer approval prompts). |
| `--json` | Request structured JSON (sets Gemini's JSON response mode). |
| `--schema PATH` | A JSON Schema file; implies `--json`; enforced at the API boundary. |
| `--session PATH` | Multi-turn: prior turns are read, the new turn sent, the response appended. |
| `--model ROLE\|ID` | A logical role (`text`, `vision`, `video`, `image`, `image_pro`, `reason`) or an explicit model ID. |
| `--size SIZE` | (`image` only) Output resolution — one of `512`, `1K`, `2K`, `4K`. |
| `--aspect-ratio RATIO` | (`image` only) Aspect ratio hint: `1:1`, `16:9`, `4:3`, etc. |
| `--endpoint VALUE` | (`image` only) Force the generation backend: `auto` (default), `interactions`, or `generate_content`. See below. |
| `--cleanup` | (session commands) Delete this session's uploaded Files API objects. |
| `--debug` | Print a traceback to **stderr** on failure (stdout stays clean JSON). |

### Image endpoint (Interactions vs generateContent)

The `image` subcommand supports two generation backends, selectable via
`--endpoint` on the CLI or `[image].endpoint` in the config:

| Value | Behaviour |
|---|---|
| `auto` *(default)* | Try the `interactions` surface first; fall back to `generate_content` automatically if it fails. |
| `interactions` | Force the Interactions API (Beta). Best quality and 4K support; no free tier for `image_pro`. |
| `generate_content` | Force the classic `generate_content` path. |

The `interactions` path is the primary route as of 2026-06-20. Both paths
produce the same JSON envelope; a `warnings` entry is added when an automatic
fallback occurs so you know which backend was actually used.

```sh
# Pro model, 4K output, forced Interactions path
gemini-delegate image --model image_pro --endpoint interactions \
  --size 4K --aspect-ratio 16:9 \
  --prompt "A red maple leaf on white, studio lighting" --out leaf.png

# Explicit fallback for debugging
gemini-delegate image --endpoint generate_content \
  --prompt "A blue circle" --out circle.png
```

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

### Example

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
also be pinned via `GEMINI_DELEGATE_MODEL_<ROLE>` (handy for a one-off run).

Costs default to the cheap Flash-tier roles; `image_pro` (Nano Banana Pro) and
`reason` are opt-in only.

## Onboarding a project (the project hook)

A consuming project can steer the subagent's output **without any code change**:
drop a `./.claude/gemini.md` describing the desired output shape (e.g. a JSON
Schema for extracted fields), naming conventions, and where generated assets go.
The subagent reads it — together with the project `CLAUDE.md` — before every
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
make test          # offline unit tests; the Gemini client is mocked (no network, no key)
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
  config.py   load TOML + env overrides, resolve model roles
  media.py    inline vs Files API, sha256 upload cache, cleanup
  session.py  session JSON read / append / write
  core.py     all Gemini logic; returns plain dicts
  cli.py      Click commands; wraps core into the envelope
agents/gemini-delegate.md     the subagent (installed to ~/.claude/agents/)
examples/project-gemini.md    sample .claude/gemini.md for a consuming project
scripts/smoke_test.py         gated live smoke test
tests/                        offline unit tests (mocked client)
```

## License

MIT
