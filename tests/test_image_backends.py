from types import SimpleNamespace
import io

import pytest
from PIL import Image

from gemini_delegate.core import CoreError
from gemini_delegate.image_backends import ImageRequest, ImageResult, run_image, GenerateContentImageBackend


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
    client = _StubBackend()
    client.generate_content = lambda **kwargs: _gc_response(1)
    from unittest.mock import MagicMock
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
    from unittest.mock import MagicMock
    client = MagicMock()
    client.models.generate_content.return_value = _gc_response(2)
    req = ImageRequest(prompt="x", model_id="m", n=2)
    result = GenerateContentImageBackend().generate(client, req)
    assert len(result.images) == 2
    cfg = client.models.generate_content.call_args.kwargs["config"]
    assert cfg.candidate_count == 2
