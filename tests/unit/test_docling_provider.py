"""docling-serve adapter behavior, exercised over a simulated transport."""

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
from acessilia_toolbox.providers.docling import DoclingProvider

DOCUMENT = {
    "texts": [
        {
            "self_ref": "#/texts/0",
            "label": "title",
            "text": "Documento",
            "level": 1,
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"left": 0, "top": 20, "right": 200, "bottom": 80},
                    "charspan": [0, 9],
                }
            ],
        }
    ],
    "pages": {"1": {"size": {"width": 595, "height": 842}}},
}


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "docling",
        "version": "1.32",
        "endpoint": "http://docling-serve:5001",
        "capabilities": ["document.structure.extract"],
        "timeout_seconds": 5.0,
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def provider_with(handler) -> DoclingProvider:
    adapter = DoclingProvider(descriptor())
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
                "python": "cpython-312",
            },
        )
    return httpx.Response(200, json={"document": {"json_content": DOCUMENT}})


def extract(adapter: DoclingProvider):
    return adapter.execute(
        "document.structure.extract",
        b"%PDF-test",
        filename="sample.pdf",
        media_type="application/pdf",
    )


def test_extraction_wraps_the_document_for_the_builder() -> None:
    extraction = extract(provider_with(convert_handler))

    assert extraction.backend == "docling"
    assert extraction.version == "1.32.0"
    assert extraction.document.num_pages() == 1
    items = list(extraction.document.iterate_items())
    assert items[0][0].label.value == "title"


def test_component_versions_are_captured_for_cache_invalidation() -> None:
    """docling-serve keys versions by package name, not under `version`."""
    components = extract(provider_with(convert_handler)).configuration["component_versions"]

    assert components["docling"] == "2.124.0"
    assert components["docling-core"] == "2.93.0"
    # Unrelated runtime details stay out of the cache key.
    assert "python" not in components


def test_version_falls_back_when_the_endpoint_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(500)
        return httpx.Response(200, json={"document": {"json_content": DOCUMENT}})

    assert extract(provider_with(handler)).version == "1.32"


def test_zero_valued_bbox_edges_survive_adaptation() -> None:
    """`left` of 0 is a real coordinate and must not fall back to a default."""
    extraction = extract(provider_with(convert_handler))
    item, _ = next(iter(extraction.document.iterate_items()))

    assert item.prov[0].bbox.l == 0
    assert item.prov[0].bbox.r == 200


def test_payload_without_wrapper_is_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(404)
        return httpx.Response(200, json=DOCUMENT)

    extraction = extract(provider_with(handler))

    assert extraction.document.num_pages() == 1
    assert extraction.version == "1.32"


def test_http_error_is_normalized_as_execution_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "unsupported"})

    with pytest.raises(ProviderExecutionError) as error:
        extract(provider_with(handler))

    assert error.value.code == "provider_execution_failed"
    assert error.value.details["status_code"] == 422


def test_timeout_is_normalized() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    with pytest.raises(ProviderTimeoutError) as error:
        extract(provider_with(handler))

    assert error.value.code == "provider_timeout"


def test_connection_failure_is_normalized_as_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderUnavailableError) as error:
        extract(provider_with(handler))

    assert error.value.code == "provider_unavailable"


def test_empty_document_content_is_rejected() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"document": {"json_content": None}})

    with pytest.raises(ProviderExecutionError):
        extract(provider_with(handler))


def test_health_reports_reachability() -> None:
    healthy = provider_with(convert_handler).health()
    assert healthy.healthy
    assert healthy.provider == "docling"
    assert healthy.version == "1.32.0"

    def failing(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    unhealthy = provider_with(failing).health()
    assert not unhealthy.healthy
    assert unhealthy.detail


def test_factory_resolves_the_registered_adapter() -> None:
    assert isinstance(create_adapter(descriptor()), DoclingProvider)


def test_factory_rejects_providers_without_an_adapter() -> None:
    from acessilia_toolbox.core.errors import ProviderNotFoundError

    with pytest.raises(ProviderNotFoundError):
        create_adapter(descriptor(id="mineru", capabilities=["document.ocr"]))
