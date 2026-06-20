from types import SimpleNamespace

import pytest

from gemini_delegate.core import CoreError
from gemini_delegate.image_backends import ImageRequest, ImageResult, run_image


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
