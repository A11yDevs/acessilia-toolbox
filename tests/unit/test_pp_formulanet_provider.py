"""pp-formulanet adapter behavior, exercised over a simulated transport."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import jsonschema
import pytest

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.pp_formulanet import PPFormulaNetProvider

SAMPLE_RESPONSE = {
    "latex": r"A=\begin{pmatrix}a&b\\c&d\end{pmatrix}",
    "bbox": [10, 20, 210, 50],
}


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "pp-formulanet",
        "version": "1.0",
        "endpoint": "http://pp-formulanet:5010",
        "capabilities": ["math.recognize"],
        "timeout_seconds": 5.0,
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def provider_with(handler) -> PPFormulaNetProvider:
    adapter = PPFormulaNetProvider(descriptor())
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
                "model": "PP-FormulaNet_plus-M",
                "paddleocr": "3.7.0",
                "paddlepaddle": "3.3.1",
            },
        )
    return httpx.Response(200, json=SAMPLE_RESPONSE)


def extract(adapter: PPFormulaNetProvider):
    return adapter.execute(
        "math.recognize",
        b"fake-image-data",
        filename="formula.png",
        media_type="image/png",
    )


class TestPPFormulaNetProvider:
    def test_extraction_returns_latex_and_bbox(self) -> None:
        extraction = extract(provider_with(predict_handler))

        assert extraction.backend == "pp-formulanet"
        assert extraction.version == "0.1.0"
        assert extraction.document["latex"] == SAMPLE_RESPONSE["latex"]
        assert extraction.document["bbox"] == [10, 20, 210, 50]
        # No calibrated confidence: the field must stay absent.
        assert "confidence" not in extraction.document

    def test_document_conforms_to_latex_schema(self) -> None:
        schema_path = Path(__file__).parents[2] / "schemas" / "latex@1.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        extraction = extract(provider_with(predict_handler))
        jsonschema.validate(instance=extraction.document, schema=schema)

    def test_configuration_names_the_provider(self) -> None:
        extraction = extract(provider_with(predict_handler))
        assert extraction.configuration["capability"] == "math.recognize"
        assert extraction.configuration["extractor"] == "pp-formulanet"

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

        health = provider_with(handler).health()
        assert health.healthy is True
        assert health.provider == "pp-formulanet"
        assert health.version == "0.1.0"

    def test_health_returns_unhealthy_on_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        health = provider_with(handler).health()
        assert health.healthy is False

    def test_versions_exposes_the_model_identity(self) -> None:
        versions = provider_with(predict_handler).versions()
        assert versions["provider"] == "0.1.0"
        assert versions["model"] == "PP-FormulaNet_plus-M"
        assert versions["paddleocr"] == "3.7.0"

    def test_create_adapter_factory(self) -> None:
        adapter = create_adapter(descriptor())
        assert isinstance(adapter, PPFormulaNetProvider)
