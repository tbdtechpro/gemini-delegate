"""Shared fakes for CLI tests (offline; the Gemini client is mocked)."""
import io
from types import SimpleNamespace

from PIL import Image


def write_png(path, color=(255, 0, 0)):
    Image.new("RGB", (8, 8), color).save(path, format="PNG")
    return path


def text_response(text, prompt_tokens=5, cand_tokens=7):
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens, candidates_token_count=cand_tokens
        ),
    )


def image_response(n=1):
    def _save_factory(payload):
        def _save(path):
            with open(path, "wb") as fh:
                fh.write(payload)
        return _save

    parts = []
    for i in range(n):
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (0, i * 20, 0)).save(buf, format="PNG")
        payload = buf.getvalue()
        parts.append(
            SimpleNamespace(
                inline_data=SimpleNamespace(data=payload),
                as_image=lambda p=payload: SimpleNamespace(save=_save_factory(p)),
            )
        )
    return SimpleNamespace(
        text=None,
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts))],
        usage_metadata=SimpleNamespace(prompt_token_count=3, candidates_token_count=0),
    )
