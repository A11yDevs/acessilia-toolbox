"""docling-layout adapter behavior, exercised over a simulated transport."""

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
from acessilia_toolbox.providers.docling_layout import DoclingLayoutProvider

SAMPLE_DOCUMENT = {
    "texts": [
        {
            "self_ref": "#/texts/0",
            "label": {"value": "title"},
            "text": "Document Title",
            "level": 1,
            "confidence": 0.98,
            "prov": [{"page": 1, "bbox": [50, 50, 500, 100]}],
        },
        {
            "self_ref": "#/texts/1",
            "label": {"value": "paragraph"},
            "text": "This is a long paragraph with enough text to be classified as text_clean. " * 10,
            "level": 1,
            "confidence": 0.95,
            "prov": [{"page": 1, "bbox": [50, 120, 500, 200]}],
        },
        {
            "self_ref": "#/texts/2",
            "label": {"value": "list"},
            "text": "Item 1\nItem 2\nItem 3",
            "level": 1,
            "confidence": 0.90,
            "prov": [{"page": 1, "bbox": [50, 220, 500, 280]}],
        },
        {
            "self_ref": "#/texts/3",
            "label": {"value": "code"},
            "text": "def hello():\n    print('world')",
            "level": 1,
            "confidence": 0.85,
            "prov": [{"page": 1, "bbox": [50, 300, 500, 360]}],
        },
    ],
    "pictures": [
        {
            "self_ref": "#/pictures/0",
            "label": {"value": "picture"},
            "confidence": 0.75,
            "prov": [{"page": 1, "bbox": [50, 380, 500, 480]}],
        },
    ],
    "tables": [
        {
            "self_ref": "#/tables/0",
            "label": {"value": "table"},
            "confidence": 0.95,
            "prov": [{"page": 1, "bbox": [50, 500, 500, 600]}],
        },
    ],
    "groups": [
        {
            "self_ref": "#/groups/0",
            "label": {"value": "formula"},
            "confidence": 0.80,
            "prov": [{"page": 1, "bbox": [50, 620, 500, 680]}],
        },
    ],
    "pages": {
        "1": {"width": 595, "height": 842},
    },
}


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "docling-layout",
        "version": "1.32",
        "endpoint": "http://docling-serve:5001",
        "capabilities": ["document.layout.analyze"],
        "timeout_seconds": 5.0,
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def provider_with(handler) -> DoclingLayoutProvider:
    adapter = DoclingLayoutProvider(descriptor())
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
    return httpx.Response(200, json=SAMPLE_DOCUMENT)


def extract(adapter: DoclingLayoutProvider):
    return adapter.execute(
        "document.layout.analyze",
        b"%PDF-test",
        filename="sample.pdf",
        media_type="application/pdf",
    )


class TestDoclingLayoutProvider:
    def test_extraction_returns_layout_analysis(self) -> None:
        extraction = extract(provider_with(convert_handler))

        assert extraction.backend == "docling"
        assert extraction.version == "1.32.0"
        assert extraction.document["page_count"] == 1
        assert extraction.document["region_count"] > 0

    def test_regions_are_classified_correctly(self) -> None:
        extraction = extract(provider_with(convert_handler))
        regions = extraction.document["pages"][0]["regions"]

        types = {r["type"] for r in regions}
        assert "text_clean" in types
        assert "list_block" in types
        assert "code_block" in types
        assert "embedded_image" in types
        assert "table" in types
        assert "formula" in types

    def test_each_region_has_required_fields(self) -> None:
        extraction = extract(provider_with(convert_handler))
        regions = extraction.document["pages"][0]["regions"]

        for region in regions:
            assert "type" in region
            assert "bbox" in region
            assert "page_number" in region
            assert "confidence" in region
            assert len(region["bbox"]) == 4

    def test_page_has_dimensions(self) -> None:
        extraction = extract(provider_with(convert_handler))
        page = extraction.document["pages"][0]

        assert page["width"] == 595
        assert page["height"] == 842
        assert page["page_number"] == 1

    def test_component_versions_are_captured(self) -> None:
        components = extract(provider_with(convert_handler)).configuration["component_versions"]

        assert components["docling"] == "2.124.0"
        assert components["docling-core"] == "2.93.0"

    def test_version_falls_back_when_endpoint_unavailable(self) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/version":
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json=SAMPLE_DOCUMENT)

        extraction = extract(provider_with(_handler))
        assert extraction.version == "1.32"  # fallback to descriptor version

    def test_health_returns_healthy(self) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok"})

        adapter = provider_with(_handler)
        health = adapter.health()
        assert health.healthy is True
        assert health.provider == "docling-layout"

    def test_health_returns_unhealthy_on_error(self) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        adapter = provider_with(_handler)
        health = adapter.health()
        assert health.healthy is False

    def test_timeout_raises_provider_timeout(self) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timeout")

        adapter = provider_with(_handler)
        with pytest.raises(ProviderTimeoutError):
            extract(adapter)

    def test_http_error_raises_execution_error(self) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/version":
                return httpx.Response(200, json={})
            return httpx.Response(500, json={"error": "internal"})

        adapter = provider_with(_handler)
        with pytest.raises(ProviderExecutionError):
            extract(adapter)

    def test_connection_error_raises_unavailable(self) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        adapter = provider_with(_handler)
        with pytest.raises(ProviderUnavailableError):
            extract(adapter)

    def test_versions_returns_provider_info(self) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "docling-serve": "1.32.0",
                    "docling": "2.124.0",
                },
            )

        adapter = provider_with(_handler)
        versions = adapter.versions()
        assert versions["provider"] == "1.32.0"

    def test_create_adapter_via_registry(self) -> None:
        adapter = create_adapter(descriptor())
        assert isinstance(adapter, DoclingLayoutProvider)

    def test_empty_document_returns_no_regions(self) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/version":
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"pages": {}})

        extraction = extract(provider_with(_handler))
        assert extraction.document["page_count"] == 0
        assert extraction.document["region_count"] == 0

    def test_ignore_classification_omits_region(self) -> None:
        doc = {
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "label": {"value": "paragraph"},
                    "text": "ab",
                    "confidence": 0.1,
                    "prov": [{"page": 1, "bbox": [0, 0, 5, 5]}],
                },
            ],
            "pages": {"1": {"width": 595, "height": 842}},
        }

        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/version":
                return httpx.Response(200, json={})
            return httpx.Response(200, json=doc)

        extraction = extract(provider_with(_handler))
        assert extraction.document["region_count"] == 0