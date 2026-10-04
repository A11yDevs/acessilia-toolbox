"""HTTP errors, model identity, and geometry through the real normalization builder."""

from __future__ import annotations

import json

import httpx
import pytest

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.normalization.builder import _build_elements, _build_pages
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.teleocr import TeleOCRProvider
from acessilia_toolbox.providers.teleocr_document import TeleOCRDocument

PAGE = {
    "size": [1000, 2000],
    "infer_size": [1200, 2400],
    "blocks": [
        {"type": "text", "bbox": [0.1, 0.2, 0.8, 0.3], "angle": 90, "content": "Sample"},
        {
            "type": "table",
            "bbox": [0.1, 0.4, 0.8, 0.6],
            "content": "<table><tr><td>A</td></tr></table>",
        },
        {"type": "equation", "bbox": [0.1, 0.7, 0.5, 0.8], "content": "x^2"},
        {"type": "page_number", "bbox": [0.8, 0.9, 0.9, 0.99], "content": "2"},
        {"type": "future-label", "bbox": None, "content": "Preserved"},
    ],
}


def descriptor() -> ProviderDescriptor:
    return ProviderDescriptor(
        id="teleocr",
        version="1.2b",
        endpoint="http://teleocr:5005",
        capabilities=["document.structure.extract"],
    )


def provider_with(handler) -> TeleOCRProvider:
    provider = TeleOCRProvider(descriptor())
    provider._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        base_url=provider.base_url,
        transport=httpx.MockTransport(handler),
    )
    return provider


def version_response() -> httpx.Response:
    return httpx.Response(
        200, json={"version": "1.2b", "model": "teleocr", "model_revision": "abc"}
    )


def test_factory_and_model_identity() -> None:
    assert isinstance(create_adapter(descriptor()), TeleOCRProvider)
    adapter = provider_with(lambda _: version_response())
    versions = adapter.versions()
    assert versions["provider"] == "1.2b"
    assert versions["model"] == "teleocr"
    assert versions["model_revision"] == "abc"
    assert json.loads(versions["inference_configuration"])["min_long"] == 1600


def test_missing_model_identity_cannot_reuse_cache_or_claim_health() -> None:
    adapter = provider_with(lambda _: httpx.Response(200, json={"version": "1.2b"}))
    first, second = adapter.versions(), adapter.versions()
    assert first["model_revision"].startswith("unavailable:")
    assert first["model_revision"] != second["model_revision"]
    assert not adapter.health().healthy


def test_missing_model_identity_rejects_inference_result() -> None:
    adapter = provider_with(
        lambda r: httpx.Response(200, json=PAGE if r.url.path == "/predict" else {})
    )
    with pytest.raises(ProviderUnavailableError, match="model_revision"):
        adapter.execute(
            "document.structure.extract", b"image", filename="page.png", media_type="image/png"
        )


def test_transport_parameters_and_normalized_geometry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return version_response()
        assert request.url.path == "/predict"
        assert b'"min_long": 1600' in request.content
        return httpx.Response(200, json=PAGE)

    result = provider_with(handler).execute(
        "document.structure.extract", b"image", filename="page.png", media_type="image/png"
    )
    assert result.backend == "teleocr"
    assert result.configuration["model_revision"] == "abc"
    elements = _build_elements(result.document)
    pages = _build_pages(result.document, elements)
    assert pages[0].width == 1000 and pages[0].height == 2000
    assert len(elements) == 5
    box = elements[0].provenance[0].bbox
    assert box is not None
    assert (box.left, box.top, box.right, box.bottom) == (100, 400, 800, 600)
    assert box.coord_origin == "TOPLEFT"
    assert elements[0].metadata["rotation_degrees"] == 90
    assert all(e.confidence is None for e in elements)
    assert elements[1].type == "table" and elements[1].text == PAGE["blocks"][1]["content"]
    assert elements[2].type == "formula" and elements[2].text == "x^2"
    assert elements[3].type == "page_footer" and elements[3].raw_label == "page_number"
    assert elements[4].type == "unknown" and elements[4].text == "Preserved"


def test_polygon_and_empty_image_page() -> None:
    doc = TeleOCRDocument(
        {"size": [10, 20], "blocks": [{"type": "image", "bbox": [0, 0, 1, 0, 1, 1, 0, 1]}]}
    )
    element = _build_elements(doc)[0]
    assert element.type == "picture" and element.text is None
    assert element.provenance[0].bbox.right == 10
    assert TeleOCRDocument({"size": [10, 20], "blocks": []}).num_pages() == 1


@pytest.mark.parametrize(
    "page",
    [
        {"size": [0, 10], "blocks": []},
        {"size": [10, 10], "blocks": [{"type": "text", "bbox": [0, 0, 2, 1]}]},
        {"size": [10, 10], "blocks": [{"type": "text", "bbox": [1, 0, 0, 1]}]},
        {"size": [10, 10], "blocks": [{"type": "text", "bbox": [0, 0, float("nan"), 1]}]},
        {"size": [10, 10], "blocks": [{"type": "text", "angle": 45}]},
    ],
)
def test_malformed_backend_payload_rejected(page) -> None:
    adapter = provider_with(lambda _: httpx.Response(200, json=json.loads(json.dumps(page))))
    with pytest.raises(ProviderExecutionError, match="response shape"):
        adapter.execute(
            "document.structure.extract", b"image", filename="a.png", media_type="image/png"
        )


@pytest.mark.parametrize(
    "exception,error",
    [
        (httpx.ReadTimeout("slow"), ProviderTimeoutError),
        (httpx.ConnectError("offline"), ProviderUnavailableError),
    ],
)
def test_transport_error_types(exception, error) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise exception

    with pytest.raises(error):
        provider_with(handler).execute(
            "document.structure.extract", b"image", filename="a.png", media_type="image/png"
        )


def test_status_error_and_health_failure() -> None:
    adapter = provider_with(lambda _: httpx.Response(503, text="starting"))
    assert adapter.health().healthy is False
    with pytest.raises(ProviderExecutionError):
        adapter.execute(
            "document.structure.extract", b"image", filename="a.png", media_type="image/png"
        )


def test_invalid_parameters_rejected_before_request() -> None:
    adapter = provider_with(lambda _: pytest.fail("invalid request must not reach backend"))
    with pytest.raises(ProviderExecutionError, match="parameters"):
        adapter.execute(
            "document.structure.extract",
            b"image",
            filename="a.png",
            media_type="image/png",
            parameters={"min_long": 3000, "max_long": 2000},
        )
    with pytest.raises(ProviderExecutionError, match="raster"):
        adapter.execute(
            "document.structure.extract", b"pdf", filename="a.pdf", media_type="application/pdf"
        )
