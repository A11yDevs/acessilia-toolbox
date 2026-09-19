"""rapid-latex-ocr adapter behavior, exercised over a simulated transport."""

from __future__ import annotations

import httpx
import pytest

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.rapid_latex_ocr import RapidLatexOcrProvider

SAMPLE_RESPONSE = {
    "latex": r"x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}",
    "confidence": 0.98,
    "bbox": [10, 20, 210, 50],
}


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "rapid-latex-ocr",
        "version": "0.1",
        "endpoint": "http://rapid-latex-ocr:5004",
        "capabilities": ["math.recognize"],
        "timeout_seconds": 5.0,
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def provider_with(handler) -> RapidLatexOcrProvider:
    adapter = RapidLatexOcrProvider(descriptor())
    transport = httpx.MockTransport(handler)
    adapter._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=transport, base_url=adapter.base_url
    )
    return adapter


def predict_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/version":
        return httpx.Response(
            200,
            json={
                "version": "0.1.0",
                "rapid_latex_ocr": "0.1.1",
            },
        )
    return httpx.Response(200, json=SAMPLE_RESPONSE)


def extract(adapter: RapidLatexOcrProvider):
    return adapter.execute(
        "math.recognize",
        b"fake-image-data",
        filename="formula.png",
        media_type="image/png",
    )


class TestRapidLatexOcrProvider:
    def test_extraction_returns_latex_and_confidence(self) -> None:
        extraction = extract(provider_with(predict_handler))

        assert extraction.backend == "rapid-latex-ocr"
        assert extraction.version == "0.1.0"
        assert extraction.document["latex"] == r"x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}"
        assert extraction.document["confidence"] == 0.98
        assert extraction.document["bbox"] == [10, 20, 210, 50]

    def test_configuration_includes_capability(self) -> None:
        extraction = extract(provider_with(predict_handler))
        assert extraction.configuration["capability"] == "math.recognize"
        assert extraction.configuration["extractor"] == "rapid-latex-ocr"

    def test_timeout_raises_provider_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        with pytest.raises(ProviderTimeoutError):
            extract(provider_with(handler))

    def test_http_error_raises_provider_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422)

        with pytest.raises(ProviderExecutionError):
            extract(provider_with(handler))

    def test_connection_error_raises_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(ProviderUnavailableError):
            extract(provider_with(handler))

    def test_health_returns_healthy(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"status": "ok", "version": "0.1.0"},
                headers={"content-type": "application/json"},
            )

        adapter = provider_with(handler)
        health = adapter.health()
        assert health.healthy is True
        assert health.provider == "rapid-latex-ocr"
        assert health.version == "0.1.0"

    def test_health_returns_unhealthy_on_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        adapter = provider_with(handler)
        health = adapter.health()
        assert health.healthy is False

    def test_versions_returns_component_info(self) -> None:
        adapter = provider_with(predict_handler)
        versions = adapter.versions()
        assert versions["provider"] == "0.1.0"
        assert versions["rapid_latex_ocr"] == "0.1.1"

    def test_create_adapter_factory(self) -> None:
        adapter = create_adapter(descriptor())
        assert isinstance(adapter, RapidLatexOcrProvider)
