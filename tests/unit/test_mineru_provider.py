"""mineru-api adapter behavior, exercised over a simulated transport."""

from __future__ import annotations

import json

import httpx
import pytest

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.mineru import MineruProvider

# middle_json shape produced by the MinerU pipeline backend.
MIDDLE_JSON = {
    "_backend": "pipeline",
    "_version_name": "2.1.11",
    "pdf_info": [
        {
            "page_idx": 0,
            "page_size": [595, 842],
            "preproc_blocks": [
                {
                    "type": "title",
                    "bbox": [50.0, 20.0, 545.0, 80.0],
                    "lines": [
                        {"spans": [{"type": "text", "content": "Relatório Anual"}]}
                    ],
                },
                {
                    "type": "text",
                    "bbox": [50.0, 90.0, 545.0, 200.0],
                    "lines": [
                        {
                            "spans": [
                                {"type": "text", "content": "Resultados do "}
                            ]
                        },
                        {"spans": [{"type": "text", "content": "exercício"}]},
                    ],
                },
                {
                    "type": "table",
                    "bbox": [50.0, 210.0, 545.0, 400.0],
                    "blocks": [
                        {
                            "type": "table_body",
                            "lines": [
                                {
                                    "spans": [
                                        {
                                            "type": "table",
                                            "html": "<table><tr><td>2025</td></tr></table>",
                                        }
                                    ]
                                }
                            ],
                        }
                    ],
                },
                {
                    "type": "interline_equation",
                    "bbox": [100.0, 410.0, 500.0, 460.0],
                    "lines": [
                        {
                            "spans": [
                                {
                                    "type": "equation",
                                    "content": r"E = mc^2",
                                }
                            ]
                        }
                    ],
                },
                {
                    "type": "image",
                    "bbox": [50.0, 470.0, 300.0, 600.0],
                    "blocks": [],
                },
            ],
            "discarded_blocks": [],
        },
        {
            "page_idx": 1,
            "page_size": [595, 842],
            "preproc_blocks": [],
            "discarded_blocks": [],
        },
    ],
}

PARSE_RESPONSE = {
    "backend": "pipeline",
    "version": "2.1.11",
    "results": {
        "sample": {
            "md_content": "# Relatório Anual\n\nResultados do exercício",
            "middle_json": MIDDLE_JSON,
            "content_list": [],
        }
    },
}


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "mineru",
        "version": "2.1",
        "endpoint": "http://mineru-serve:5002",
        "capabilities": ["document.structure.extract"],
        "timeout_seconds": 5.0,
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def provider_with(handler, provider_id: str = "mineru") -> MineruProvider:
    factory = MineruProvider
    if provider_id == "mineru-layout":
        from acessilia_toolbox.providers.mineru_layout import MineruLayoutProvider

        factory = MineruLayoutProvider
    elif provider_id == "mineru-ocr":
        from acessilia_toolbox.providers.mineru_ocr import MineruOcrProvider

        factory = MineruOcrProvider
    adapter = factory(descriptor(id=provider_id))
    transport = httpx.MockTransport(handler)
    adapter._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=transport, base_url=adapter.base_url
    )
    return adapter


def parse_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/openapi.json":
        return httpx.Response(200, json={"info": {"version": "2.1.11"}})
    return httpx.Response(200, json=PARSE_RESPONSE)


def extract(adapter: MineruProvider, **params: object):
    return adapter.execute(
        "document.structure.extract",
        b"%PDF-test",
        filename="sample.pdf",
        media_type="application/pdf",
        parameters=params or None,
    )


# ── document facade ──────────────────────────────────────────────────


def _facade():
    from acessilia_toolbox.providers.mineru_document import MineruDocument

    return MineruDocument(MIDDLE_JSON)


def test_document_facade_exposes_pages() -> None:
    document = _facade()
    assert document.page_count == 2
    assert document.pages[0]["page_no"] == 0
    assert document.pages[0]["width"] == 595


def test_document_facade_exposes_texts_tables_formulas() -> None:
    document = _facade()

    texts = document.texts
    assert len(texts) == 2
    assert texts[0].label == "title"
    assert texts[0].text == "Relatório Anual"
    assert texts[1].text == "Resultados do exercício"

    tables = document.tables
    assert len(tables) == 1
    assert tables[0].html == "<table><tr><td>2025</td></tr></table>"

    formulas = document.formulas
    assert len(formulas) == 1
    assert formulas[0].latex == r"E = mc^2"

    pictures = document.pictures
    assert len(pictures) == 1


def test_document_facade_bbox_and_pages() -> None:
    document = _facade()
    title = document.texts[0]
    assert title.page_no == 0
    assert title.bbox.as_tuple() == (50.0, 20.0, 545.0, 80.0)


def test_document_facade_full_text_preserves_reading_order() -> None:
    document = _facade()
    text = document.full_text
    assert text.index("Relatório Anual") < text.index("Resultados do exercício")


# ── provider adapter ─────────────────────────────────────────────────


def test_extraction_wraps_the_middle_json() -> None:
    extraction = extract(provider_with(parse_handler))

    assert extraction.backend == "mineru"
    assert extraction.version == "2.1.11"
    document = extraction.document
    assert document.page_count == 2
    assert len(document.texts) == 2


def test_parse_request_carries_defaults_and_parameters() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json={"info": {"version": "2.1.11"}})
        captured["data"] = dict(request.url.params) if request.url.query else None
        captured["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, json=PARSE_RESPONSE)

    extract(provider_with(handler), lang="en", backend="vlm")

    assert "multipart/form-data" in str(captured["content_type"])


def test_empty_results_raise_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"backend": "pipeline", "results": {}})

    with pytest.raises(ProviderExecutionError):
        extract(provider_with(handler))


def test_missing_middle_json_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"backend": "pipeline", "results": {"sample": {"md_content": "x"}}},
        )

    with pytest.raises(ProviderExecutionError):
        extract(provider_with(handler))


def test_middle_json_as_serialized_string_is_accepted() -> None:
    """Some mineru-api builds ship middle_json as a JSON-encoded string."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "backend": "pipeline",
                "version": "2.1.11",
                "results": {
                    "sample": {
                        "md_content": "# ok",
                        "middle_json": json.dumps(MIDDLE_JSON),
                        "content_list": [],
                    }
                },
            },
        )

    extraction = extract(provider_with(handler))
    document = extraction.document

    assert document.page_count == 2
    assert len(document.texts) == 2


def test_http_error_maps_to_provider_execution_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": "bad file"})

    with pytest.raises(ProviderExecutionError) as excinfo:
        extract(provider_with(handler))
    assert "422" in str(excinfo.value)


def test_timeout_maps_to_provider_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow")

    with pytest.raises(ProviderTimeoutError):
        extract(provider_with(handler))


def test_connection_error_maps_to_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderUnavailableError):
        extract(provider_with(handler))


def test_health_uses_openapi_probe() -> None:
    health = provider_with(parse_handler).health()
    assert health.healthy is True
    assert health.version == "2.1.11"


def test_health_reports_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    health = provider_with(handler).health()
    assert health.healthy is False
    assert "ConnectError" in (health.detail or "")


def test_registry_creates_mineru_adapters() -> None:
    for provider_id in ("mineru", "mineru-layout", "mineru-ocr"):
        adapter = create_adapter(descriptor(id=provider_id))
        assert adapter.descriptor.id == provider_id


# ── layout provider ──────────────────────────────────────────────────


def test_layout_provider_classifies_regions() -> None:
    extraction = extract(provider_with(parse_handler, "mineru-layout"))

    regions = extraction.configuration["layout_regions"]
    types = {region["type"] for region in regions}
    assert {"title", "text", "table", "formula", "embedded_image"} <= types
    assert regions[0]["page"] == 0
    assert extraction.configuration["region_count"] == len(regions)
    assert extraction.configuration["backend"] == "mineru-layout"


# ── ocr provider ─────────────────────────────────────────────────────


def test_ocr_provider_flattens_text_items() -> None:
    extraction = extract(provider_with(parse_handler, "mineru-ocr"), lang="ch")

    items = extraction.configuration["ocr_items"]
    assert len(items) == 2
    assert items[0]["text"] == "Relatório Anual"
    assert items[0]["page"] == 0
    assert "Relatório Anual" in extraction.configuration["full_text"]
    assert extraction.configuration["language"] == "ch"
    assert extraction.configuration["backend"] == "mineru-ocr"
