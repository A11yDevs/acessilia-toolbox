"""docling-ocr adapter behavior, exercised over a simulated transport."""
# mypy: disable-error-code="index"

from collections.abc import Callable

import httpx
import pytest

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.docling_ocr import DoclingOcrProvider

SAMPLE_RESPONSE = {
    "document": {
        "json_content": {
            "schema_name": "DoclingDocument",
            "version": "1.0",
            "name": "test",
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "label": {"value": "paragraph"},
                    "text": "Este é um texto de teste em português.",
                    "confidence": 0.95,
                    "prov": [{"page": 1, "bbox": [50, 50, 500, 100]}],
                },
                {
                    "self_ref": "#/texts/1",
                    "label": {"value": "paragraph"},
                    "text": "Segundo parágrafo com mais conteúdo.",
                    "confidence": 0.90,
                    "prov": [{"page": 1, "bbox": [50, 120, 500, 200]}],
                },
            ],
            "groups": [],
            "pictures": [],
            "tables": [],
            "pages": {
                "1": {"size": {"width": 595, "height": 842}},
            },
        }
    },
    "status": "success",
    "errors": [],
    "processing_time": 2.0,
    "timings": {},
    "confidence": 0.95,
}


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "docling-ocr",
        "version": "1.32",
        "endpoint": "http://docling-serve:5001",
        "capabilities": ["document.ocr"],
        "timeout_seconds": 5.0,
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def provider_with(handler: Callable[[httpx.Request], httpx.Response]) -> DoclingOcrProvider:
    adapter = DoclingOcrProvider(descriptor())
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


def extract(adapter: DoclingOcrProvider) -> ExtractionResult:
    return adapter.execute(
        "document.ocr",
        b"fake-image-data",
        filename="scan.png",
        media_type="image/png",
    )


class TestDoclingOcrProvider:
    def test_extraction_returns_ocr_items(self) -> None:
        extraction = extract(provider_with(convert_handler))

        assert extraction.backend == "docling"
        assert extraction.version == "1.32.0"
        assert extraction.document["item_count"] == 2

    def test_ocr_items_have_text_and_confidence(self) -> None:
        extraction = extract(provider_with(convert_handler))
        items = extraction.document["items"]

        assert items[0]["text"] == "Este é um texto de teste em português."
        assert items[0]["confidence"] == 0.95
        assert items[1]["text"] == "Segundo parágrafo com mais conteúdo."

    def test_ocr_items_have_page_and_bbox(self) -> None:
        extraction = extract(provider_with(convert_handler))
        items = extraction.document["items"]

        assert items[0]["page"] == 1
        assert len(items[0]["bbox"]) == 4

    def test_full_text_concatenates_items(self) -> None:
        extraction = extract(provider_with(convert_handler))

        assert "Este é um texto" in extraction.document["full_text"]
        assert "Segundo parágrafo" in extraction.document["full_text"]

    def test_empty_response_returns_no_items(self) -> None:
        empty = dict(SAMPLE_RESPONSE)
        empty["document"]["json_content"]["texts"] = []
        empty["document"]["json_content"]["groups"] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/version":
                return httpx.Response(200, json={"docling-serve": "1.32.0"})
            return httpx.Response(200, json=empty)

        extraction = extract(provider_with(handler))
        assert extraction.document["item_count"] == 0
        assert extraction.document["items"] == []

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
        assert health.provider == "docling-ocr"

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

    def test_create_adapter_factory(self) -> None:
        adapter = create_adapter(descriptor())
        assert isinstance(adapter, DoclingOcrProvider)

    def test_configuration_includes_ocr_params(self) -> None:
        extraction = extract(provider_with(convert_handler))
        assert extraction.configuration["capability"] == "document.ocr"
        assert extraction.configuration["force_ocr"] is True
        assert extraction.configuration["ocr_lang"] == "pt-BR"

    def test_label_as_string_not_dict(self) -> None:
        response = dict(SAMPLE_RESPONSE)
        jc = response["document"]["json_content"]
        jc["texts"] = [
            {
                "self_ref": "#/texts/0",
                "label": "paragraph",
                "text": "Texto simples.",
                "confidence": 0.9,
                "prov": [{"page": 1, "bbox": [0, 0, 100, 50]}],
            },
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/version":
                return httpx.Response(200, json={"docling-serve": "1.32.0"})
            return httpx.Response(200, json=response)

        extraction = extract(provider_with(handler))
        assert extraction.document["item_count"] == 1
        assert extraction.document["items"][0]["text"] == "Texto simples."
