from types import SimpleNamespace
import base64
import io
from unittest.mock import MagicMock

import pytest
from PIL import Image

from gemini_delegate.core import CoreError
from gemini_delegate.image_backends import ImageRequest, ImageResult, run_image, GenerateContentImageBackend, InteractionsImageBackend


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


def test_generate_content_backend_adds_ref_images(tmp_path):
    # Write a small PNG to disk — small enough to go inline (no upload).
    ref_png = tmp_path / "ref.png"
    ref_png.write_bytes(_png_bytes((50, 100, 150)))

    client = MagicMock()
    client.models.generate_content.return_value = _gc_response(1)
    req = ImageRequest(prompt="x", model_id="m", refs=[str(ref_png)])
    GenerateContentImageBackend().generate(client, req)

    call_kwargs = client.models.generate_content.call_args.kwargs
    contents = call_kwargs["contents"]
    # contents is a list with one Content; its parts should include the prompt
    # text part AND at least one ref image part.
    all_parts = [p for content in contents for p in content.parts]
    assert len(all_parts) > 1, "expected prompt part + at least one ref image part"


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
