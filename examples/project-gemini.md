# Gemini delegation conventions for THIS project

Copy this file to `./.claude/gemini.md` in a consuming project and edit it. The
`gemini-delegate` subagent reads it (and the project `CLAUDE.md`) before every
delegation, so it shapes Gemini's output to your project *without any code
change*. Keep it short and concrete — it is instructions, not prose.

---

## Output format

State what you want back and in what shape. Prefer a JSON Schema so structure is
enforced at the API boundary via `--schema`.

- For image/video understanding, return **structured JSON** matching
  `./.claude/schemas/asset.json` (below). Use:
  `gemini-delegate describe <img> --prompt "…" --schema .claude/schemas/asset.json`
- For free-form answers, a tight paragraph is fine — no preamble, no "Here is…".

Example schema (`./.claude/schemas/asset.json`):

```json
{
  "type": "object",
  "properties": {
    "title":       { "type": "string" },
    "description": { "type": "string" },
    "tags":        { "type": "array", "items": { "type": "string" } },
    "text_content":{ "type": "string", "description": "OCR'd text, verbatim" },
    "has_people":  { "type": "boolean" }
  },
  "required": ["title", "description", "tags"]
}
```

## Naming & locations for generated assets

- Write generated images to `./assets/generated/` with a kebab-case,
  content-descriptive filename, e.g. `--out assets/generated/hero-banner.png`.
- Never overwrite an existing asset; if the path exists, append `-v2`, `-v3`, …
- Generated images carry a SynthID watermark — note this in any PR that adds one.

## Model / cost policy

- Default to the cheap Flash-tier roles (the CLI's defaults). Do **not** pass
  `--model image_pro` or `--model reason` unless a task explicitly needs 4K
  fidelity, dense text rendering, or hard structured extraction.
- Surface `usage` (token counts) and the `model` used when you report back, so
  cost is visible.

## Validation expectations

- Reject refusals and boilerplate; the description must be about *our* asset.
- For structured output, all `required` fields above must be present and the
  right type before you return.
- For generated images, `Read` the file and confirm it matches the brief before
  handing it back.

## Multi-turn

- When iterating on one asset (e.g. refining a generated image, or asking
  follow-up questions about one video), reuse a single
  `--session .claude/sessions/<asset>.json` so context and uploads persist and
  cost stays low.
