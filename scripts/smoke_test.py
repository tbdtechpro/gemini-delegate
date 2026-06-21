#!/usr/bin/env python3
"""Live smoke test for gemini-delegate (CLAUDE.md §9.9, §10).

Makes ONE real call per subcommand against the installed `gemini-delegate`
binary and checks each envelope. This is gated and never part of `make test`:

    RUN_LIVE=1 GEMINI_API_KEY=… python scripts/smoke_test.py
    # or simply:  make smoke   (with RUN_LIVE=1 and the key in the environment)

You run this. I (Claude) only scaffold it — it is the one step that spends real
tokens and needs a real key, which is read from the environment by the CLI.

Optional env knobs:
    GEMINI_DELEGATE_BIN   path to the CLI (default: whatever is on PATH)
    SMOKE_VIDEO_URL       video URL to test, or "skip" to skip video
                          (default: a short public YouTube clip)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_VIDEO_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo", ~19s


def _gate() -> None:
    if os.environ.get("RUN_LIVE") != "1":
        print("RUN_LIVE != 1 — skipping live smoke test (this is expected during normal dev).")
        sys.exit(0)
    if not os.environ.get("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY is not set; cannot run the live smoke test.", file=sys.stderr)
        sys.exit(1)


def _find_bin() -> str:
    binary = os.environ.get("GEMINI_DELEGATE_BIN") or shutil.which("gemini-delegate")
    if not binary:
        print(
            "ERROR: `gemini-delegate` is not on PATH. Install it first:\n"
            "  pipx install --editable .   (or set GEMINI_DELEGATE_BIN)",
            file=sys.stderr,
        )
        sys.exit(1)
    return binary


def _call(binary: str, args: list[str]) -> tuple[int, dict | None, str, str]:
    proc = subprocess.run([binary, *args], capture_output=True, text=True)
    try:
        env = json.loads(proc.stdout)
    except json.JSONDecodeError:
        env = None
    return proc.returncode, env, proc.stdout, proc.stderr


def _report(name: str, code: int, env: dict | None, stdout: str, stderr: str, extra_ok: bool = True) -> bool:
    passed = code == 0 and isinstance(env, dict) and env.get("ok") is True and extra_ok
    mark = "PASS" if passed else "FAIL"
    print(f"[{mark}] {name}")
    if isinstance(env, dict):
        model = env.get("model")
        usage = env.get("usage") or {}
        print(f"        model={model}  usage={usage}")
        if env.get("warnings"):
            print(f"        warnings={env['warnings']}")
        text = env.get("text")
        if text:
            snippet = text if len(text) <= 160 else text[:157] + "..."
            print(f"        text={snippet!r}")
        if env.get("files"):
            print(f"        files={env['files']}")
        if not passed and env.get("error"):
            print(f"        error={env['error']}")
    else:
        print(f"        exit={code}; stdout did not parse as an envelope")
        if stderr.strip():
            print(f"        stderr={stderr.strip()[:300]}")
    return passed


def main() -> int:
    _gate()
    binary = _find_bin()
    print(f"Using binary: {binary}\n")

    results: list[bool] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)

        # --- describe: needs a real image; generate a small one with Pillow ---
        try:
            from PIL import Image

            img = tmpdir / "red-square.png"
            Image.new("RGB", (96, 96), (200, 30, 30)).save(img)
            code, env, out, err = _call(
                binary,
                ["describe", str(img), "--prompt",
                 "In one short sentence, describe this image and name the dominant color."],
            )
            results.append(_report("describe", code, env, out, err))
        except ImportError:
            print("[SKIP] describe — Pillow not available to generate a test image")

        # --- ask: plain text round-trip ---
        code, env, out, err = _call(
            binary, ["ask", "--prompt", "Reply with exactly one word: pong"]
        )
        results.append(_report("ask", code, env, out, err))

        # --- image: generate to a file, then confirm the file exists ---
        out_png = tmpdir / "generated.png"
        code, env, out, err = _call(
            binary,
            ["image", "--prompt", "A simple flat red circle centered on a white background",
             "--out", str(out_png)],
        )
        file_ok = out_png.is_file() and out_png.stat().st_size > 0
        results.append(_report("image", code, env, out, err, extra_ok=file_ok))
        if isinstance(env, dict) and env.get("ok") and not file_ok:
            print("        NOTE: envelope ok but no non-empty file at --out; "
                  "image config (e.g. response_modalities) may need adjustment.")

        # --- image (transparent): opt-in, gated behind SMOKE_TRANSPARENT=1 ---
        if os.environ.get("SMOKE_TRANSPARENT") == "1":
            out_t = tmpdir / "transparent.png"
            code, env, out, err = _call(
                binary,
                ["image", "--transparent", "--prompt",
                 "A simple solid blue circle", "--out", str(out_t)],
            )
            ok = out_t.is_file()
            if ok:
                from PIL import Image
                im = Image.open(out_t)
                ok = im.mode == "RGBA" and im.getpixel((0, 0))[3] == 0
            results.append(_report("image:transparent", code, env, out, err, extra_ok=ok))

        # --- image (Pro / 4K / Interactions): opt-in, gated behind SMOKE_PRO=1 ---
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

        # --- video: YouTube URL pass-through (skippable) ---
        video_url = os.environ.get("SMOKE_VIDEO_URL", DEFAULT_VIDEO_URL)
        if video_url.lower() == "skip":
            print("[SKIP] video — SMOKE_VIDEO_URL=skip")
        else:
            code, env, out, err = _call(
                binary,
                ["video", video_url, "--prompt", "In one sentence, what happens in this video?"],
            )
            results.append(_report("video", code, env, out, err))

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} live checks passed.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
