"""Click commands: the thin envelope/exit-code layer over core (CLAUDE.md §4, §5).

Every command prints exactly one JSON envelope to stdout and sets its exit code:
0 on success, 1 on failure, 2 on a usage/argument error (Click handles most of
those itself). No bare traceback ever reaches stdout; with --debug it goes to
stderr. The API key is never read here and never echoed.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

import click

from . import core, media
from .config import load_config
from .session import Session
from . import __version__

# Options shared by the three text-producing, session-capable commands.
def _text_options(func: Callable) -> Callable:
    func = click.option("--prompt", default=None, help="The instruction to send.")(func)
    func = click.option(
        "--prompt-file", "prompt_file", type=click.Path(exists=True, dir_okay=False),
        help="Read the prompt from a file (use for long/multi-line prompts to avoid "
             "shell quoting and approval friction).",
    )(func)
    func = click.option("--json", "want_json", is_flag=True, help="Request JSON output.")(func)
    func = click.option(
        "--schema", type=click.Path(exists=True, dir_okay=False),
        help="JSON Schema file; implies --json and enforces structure at the API.",
    )(func)
    func = click.option(
        "--session", type=click.Path(dir_okay=False), help="Session file for multi-turn."
    )(func)
    func = click.option("--model", default=None, help="Logical role or explicit model ID.")(func)
    func = click.option(
        "--cleanup", is_flag=True, help="After the turn, delete this session's uploaded files."
    )(func)
    func = click.option("--debug", is_flag=True, help="Print a traceback to stderr on failure.")(func)
    return func


@click.group()
@click.version_option(__version__, prog_name="gemini-delegate")
def cli() -> None:
    """Delegate multimodal work to the Google Gemini API."""


@cli.command()
@click.argument("images", nargs=-1, type=click.Path(exists=True, dir_okay=False))
@_text_options
def describe(images, prompt, prompt_file, want_json, schema, session, model, cleanup, debug):
    """Image(s) -> text/JSON."""
    if not images:
        raise click.UsageError("describe requires at least one image path")
    prompt = _resolve_prompt(prompt, prompt_file)

    def run(client):
        result = core.describe(
            client, load_config(), images=list(images), prompt=prompt,
            model=model, want_json=want_json, schema=schema, session_path=session,
        )
        _maybe_cleanup(client, session, cleanup, result)
        return result

    _emit("describe", debug, run)


@cli.command()
@click.argument("src")
@_text_options
def video(src, prompt, prompt_file, want_json, schema, session, model, cleanup, debug):
    """Video file or URL -> text/JSON."""
    if not media.is_url(src) and not Path(src).is_file():
        raise click.UsageError(f"video source not found: {src}")
    prompt = _resolve_prompt(prompt, prompt_file)

    def run(client):
        result = core.video(
            client, load_config(), src=src, prompt=prompt,
            model=model, want_json=want_json, schema=schema, session_path=session,
        )
        _maybe_cleanup(client, session, cleanup, result)
        return result

    _emit("video", debug, run)


@cli.command()
@_text_options
def ask(prompt, prompt_file, want_json, schema, session, model, cleanup, debug):
    """Text prompt (+ session history) -> text/JSON."""
    prompt = _resolve_prompt(prompt, prompt_file)

    def run(client):
        result = core.ask(
            client, load_config(), prompt=prompt,
            model=model, want_json=want_json, schema=schema, session_path=session,
        )
        _maybe_cleanup(client, session, cleanup, result)
        return result

    _emit("ask", debug, run)


@cli.command()
@click.option("--prompt", default=None, help="The image description.")
@click.option(
    "--prompt-file", "prompt_file", type=click.Path(exists=True, dir_okay=False),
    help="Read the prompt from a file (for long/multi-line prompts).",
)
@click.option("--out", required=True, type=click.Path(dir_okay=False), help="Where to write the image.")
@click.option(
    "--ref", "refs", multiple=True, type=click.Path(exists=True, dir_okay=False),
    help="Reference image for editing/consistency (repeatable).",
)
@click.option("--n", default=1, type=int, help="How many images to generate.")
@click.option("--model", default=None, help="Logical role or explicit model ID.")
@click.option("--debug", is_flag=True, help="Print a traceback to stderr on failure.")
def image(prompt, prompt_file, out, refs, n, model, debug):
    """Text (+ optional refs) -> generated image file(s)."""
    if n < 1:
        raise click.UsageError("--n must be >= 1")
    prompt = _resolve_prompt(prompt, prompt_file)

    def run(client):
        return core.image(
            client, load_config(), prompt=prompt, out=out, refs=list(refs), model=model, n=n
        )

    _emit("image", debug, run)


# --- shared helpers -------------------------------------------------------------


def _resolve_prompt(prompt: str | None, prompt_file: str | None) -> str:
    """Resolve the prompt from --prompt or --prompt-file (exactly one required)."""
    if prompt_file:
        if prompt is not None:
            raise click.UsageError("pass either --prompt or --prompt-file, not both")
        return Path(prompt_file).read_text()
    if prompt is None:
        raise click.UsageError("one of --prompt or --prompt-file is required")
    return prompt


# --- envelope plumbing ----------------------------------------------------------


def _blank(op: str) -> dict[str, Any]:
    return {
        "ok": False, "op": op, "model": None, "text": None, "json": None,
        "files": [], "session": None, "usage": {"input_tokens": 0, "output_tokens": 0},
        "warnings": [], "error": None,
    }


def _emit(op: str, debug: bool, run: Callable[[Any], dict[str, Any]]) -> None:
    """Run an operation, wrap it in the envelope, print once, and exit."""
    try:
        client = core.make_client()
        result = run(client)
        env = {**_blank(op), **result, "ok": True, "error": None}
        _print_and_exit(env, 0)
    except core.CoreError as exc:
        if debug:
            traceback.print_exc(file=sys.stderr)
        _print_and_exit(_error_envelope(op, exc), 1)
    except Exception as exc:  # noqa: BLE001 — boundary: nothing escapes as a traceback to stdout
        if debug:
            traceback.print_exc(file=sys.stderr)
        env = _blank(op)
        env["error"] = {"type": "internal", "message": str(exc)}
        _print_and_exit(env, 1)


def _error_envelope(op: str, exc: core.CoreError) -> dict[str, Any]:
    env = _blank(op)
    env["error"] = {"type": exc.type, "message": exc.message}
    details = exc.details
    if details.get("model"):
        env["model"] = details["model"]
    if details.get("usage"):
        env["usage"] = details["usage"]
    if details.get("text") is not None:
        env["text"] = details["text"]
    return env


def _print_and_exit(env: dict[str, Any], code: int) -> None:
    click.echo(json.dumps(env))
    sys.exit(code)


def _maybe_cleanup(client: Any, session_path: str | None, cleanup: bool, result: dict[str, Any]) -> None:
    """Delete a session's uploaded Files API objects on --cleanup (CLAUDE.md §6)."""
    if not (cleanup and session_path):
        return
    session = Session.load(session_path)
    deleted = media.cleanup_uploads(client, session.uploads)
    session.uploads.clear()
    session.save(session_path)
    result.setdefault("warnings", []).append(f"cleaned up {len(deleted)} uploaded file(s)")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
