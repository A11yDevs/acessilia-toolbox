"""docling-math adapter behavior, exercised over a simulated transport."""

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
from acessilia_toolbox.providers.docling_math import DoclingMathProvider

SAMPLE_RESPONSE = {
    "document": {
        "json_content": {
            "schema_name": "DoclingDocument",
            "version": "1.0",
            "name": "test",
            "origin": "test",
            "furniture": {},
            "body": [],
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "label": {"value": "text"},
                    "text": "E = mc^2",
                    "level": 1,
                    "confidence": 0.98,
                    "prov": [{"page": 1, "bbox": [50, 50, 500, 100]}],
                },
            ],
            "groups": [
                {
                    "self_ref": "#/groups/0",
                    "label": {"value": "formula"},
                    "text": r"E = mc^2",
                    "confidence": 0.95,
                    "prov": [{"page": 1, "bbox": [50, 50, 500, 100]}],
                },
                {
                    "self_ref": "#/groups/1",
                    "label": {"value": "formula"},
                    "text": r"\frac{-b \pm \sqrt{b^2 - 4ac}}{2a}",
                    "confidence": 0.90,
                    "prov": [{"page": 2, "bbox": [50, 200, 500, 300]}],
                },
            ],
            "pictures": [],
            "tables": [],
            "key_value_items": [],
            "form_items": [],
            "pages": {
                "1": {"size": {"width": 595, "height": 842}},
                "2": {"size": {"width": 595, "height": 842}},
            },
        }
    },
    "status": "success",
    "errors": [],
    "processing_time": 1.5,
    "timings": {},
    "confidence": 0.95,
}


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "docling-math",
        "version": "1.32",
        "endpoint": "http://docling-serve:5001",
        "capabilities": ["math.recognize"],
        "timeout_seconds": 5.0,
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def provider_with(handler) -> DoclingMathProvider:
    adapter = DoclingMathProvider(descriptor())
    transport = httpx.MockTransport(handler)
    adapter._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=transport, base_url=adapter.base_url
    )
    return adapter


def convert_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/version":
        return httpx.Response(
            200,
            json={
                "docling-serve": "1.32.0",
                "docling": "2.124.0",
                "docling-core": "2.93.0",
            },
        )
    return httpx.Response(200, json=SAMPLE_RESPONSE)


def extract(adapter: DoclingMathProvider):
    return adapter.execute(
        "math.recognize",
        b"fake-image-data",
        filename="formula.png",
        media_type="image/png",
    )


class TestDoclingMathProvider:
    def test_extraction_returns_formulas(self) -> None:
        extraction = extract(provider_with(convert_handler))

        assert extraction.backend == "docling"
        assert extraction.version == "1.32.0"
        assert extraction.document["formula_count"] == 2

    def test_formulas_contain_latex(self) -> None:
        extraction = extract(provider_with(convert_handler))
        formulas = extraction.document["formulas"]

        assert formulas[0]["latex"] == r"E = mc^2"
        assert formulas[1]["latex"] == r"\frac{-b \pm \sqrt{b^2 - 4ac}}{2a}"

    def test_formulas_have_confidence_and_page(self) -> None:
        extraction = extract(provider_with(convert_handler))
        formulas = extraction.document["formulas"]

        assert formulas[0]["confidence"] == 0.95
        assert formulas[0]["page"] == 1
        assert formulas[1]["page"] == 2

    def test_formulas_have_bbox(self) -> None:
        extraction = extract(provider_with(convert_handler))
        formulas = extraction.document["formulas"]

        assert len(formulas[0]["bbox"]) == 4

    def test_raw_text_fallback(self) -> None:
        extraction = extract(provider_with(convert_handler))

        assert "E = mc^2" in extraction.document["raw_text"]

    def test_empty_response_returns_no_formulas(self) -> None:
        empty = dict(SAMPLE_RESPONSE)
        empty["document"]["json_content"]["groups"] = []
        empty["document"]["json_content"]["texts"] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/version":
                return httpx.Response(200, json={"docling-serve": "1.32.0"})
            return httpx.Response(200, json=empty)

        extraction = extract(provider_with(handler))
        assert extraction.document["formula_count"] == 0
        assert extraction.document["formulas"] == []

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
            return httpx.Response(200, json={"status": "ok"})

        adapter = provider_with(handler)
        health = adapter.health()
        assert health.healthy is True
        assert health.provider == "docling-math"

    def test_health_returns_unhealthy_on_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        adapter = provider_with(handler)
        health = adapter.health()
        assert health.healthy is False

    def test_versions_returns_component_info(self) -> None:
        adapter = provider_with(convert_handler)
        versions = adapter.versions()
        assert versions["provider"] == "1.32.0"
        assert versions["docling"] == "2.124.0"

    def test_create_adapter_factory(self) -> None:
        adapter = create_adapter(descriptor())
        assert isinstance(adapter, DoclingMathProvider)

    def test_configuration_includes_capability(self) -> None:
        extraction = extract(provider_with(convert_handler))
        assert extraction.configuration["capability"] == "math.recognize"
        assert extraction.configuration["extractor"] == "docling-serve"

    def test_label_as_string_not_dict(self) -> None:
        """Handle case where label is a plain string instead of {value: ...}."""
        response = dict(SAMPLE_RESPONSE)
        jc = response["document"]["json_content"]
        jc["groups"] = [
            {
                "self_ref": "#/groups/0",
                "label": "formula",
                "text": r"x = y",
                "confidence": 0.9,
                "prov": [{"page": 1, "bbox": [0, 0, 100, 50]}],
            },
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/version":
                return httpx.Response(200, json={"docling-serve": "1.32.0"})
            return httpx.Response(200, json=response)

        extraction = extract(provider_with(handler))
        assert extraction.document["formula_count"] == 1
        assert extraction.document["formulas"][0]["latex"] == "x = y"