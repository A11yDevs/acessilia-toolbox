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

# MinerU 4 protocol: ordered blocks and source-page geometry.
MIDDLE_JSON_V4 = {
    "schema": "docvortex.middle",
    "schema_version": "2.0",
    "metadata": {"producer": {"name": "mineru", "version": "4.0.12"}},
    "extensions": {
        "mineru": {"tier": "basic", "parse_mode": "ocr"},
        "docvortex_layout": {"pages": [
            {"page_idx": 0, "width_pt": 595, "height_pt": 842},
            {"page_idx": 1, "width_pt": 595, "height_pt": 842},
        ]},
    },
    "pages": [
        {"page_idx": 0, "blocks": [
            {"type": "title", "bbox": [50, 20, 545, 80],
             "content": [{"type": "text", "content": "Relatório Anual"}]},
            {"type": "text", "bbox": [50, 90, 545, 200],
             "content": [{"type": "text", "content": "Resultados do exercício"}]},
            {"type": "table", "bbox": [50, 210, 545, 400],
             "content": [{"type": "table", "content": "<table><tr><td>2025</td></tr></table>"}]},
            {"type": "equation", "bbox": [100, 410, 500, 460],
             "content": [{"type": "equation", "content": r"E = mc^2"}]},
            {"type": "image", "bbox": [50, 470, 300, 600]},
        ]},
        {"page_idx": 1, "blocks": []},
    ],
}

PARSE_RESPONSE = {
    "status": "completed",
    "files": [{"status": "completed", "content": {"middle_json": MIDDLE_JSON_V4}}],
}


def descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "mineru",
        "version": "4.0.12",
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
    if request.url.path == "/v1/health":
        return httpx.Response(200, json={"version": "4.0.12"})
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
    assert document.pages[1].size.width == 595
    assert document.pages[1].size.height == 842


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
    assert extraction.version == "4.0.12"
    document = extraction.document
    assert document.page_count == 2
    assert len(document.texts) == 2


def test_parse_request_carries_defaults_and_parameters() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/health":
            return httpx.Response(200, json={"version": "4.0.12"})
        captured["data"] = dict(request.url.params) if request.url.query else None
        captured["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, json=PARSE_RESPONSE)

    extract(provider_with(handler), tier="basic", ocr_mode="ocr")

    assert "multipart/form-data" in str(captured["content_type"])
    assert captured["data"]["tier"] == "basic"
    assert captured["data"]["ocr_mode"] == "ocr"
    assert captured["data"]["output_formats"] == "middle_json"


def test_empty_results_raise_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "completed", "files": []})

    with pytest.raises(ProviderExecutionError):
        extract(provider_with(handler))


def test_missing_middle_json_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "completed", "files": [{"status": "completed", "content": {}}]},
        )

    with pytest.raises(ProviderExecutionError):
        extract(provider_with(handler))


def test_middle_json_as_serialized_string_is_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "completed", "files": [
                {"status": "completed", "content": {"middle_json": json.dumps(MIDDLE_JSON_V4)}}
            ]},
        )

    extraction = extract(provider_with(handler))
    assert extraction.document.page_count == 2
    assert len(extraction.document.texts) == 2


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


def test_health_uses_v1_probe() -> None:
    health = provider_with(parse_handler).health()
    assert health.healthy is True
    assert health.version == "4.0.12"


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


def test_v4_document_preserves_geometry_reading_order_and_components() -> None:
    from acessilia_toolbox.providers.mineru_document import MineruDocument

    document = MineruDocument(MIDDLE_JSON_V4)
    assert document.pages[1].size.width == 595
    assert document.pages[1].size.height == 842
    assert [item.text for item in document.texts] == [
        "Relatório Anual", "Resultados do exercício"
    ]
    assert document.tables[0].html == "<table><tr><td>2025</td></tr></table>"
    assert document.formulas[0].latex == r"E = mc^2"
    assert document.version_name == "4.0.12"


def test_v4_async_202_uses_task_result(monkeypatch: pytest.MonkeyPatch) -> None:
    import acessilia_toolbox.providers.mineru as module

    monkeypatch.setattr(module, "sleep", lambda seconds: None)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1/file_parse":
            return httpx.Response(202, json={"task_id": "job-123"})
        return httpx.Response(200, json=PARSE_RESPONSE)

    assert extract(provider_with(handler)).document.page_count == 2
    assert requests == ["/v1/file_parse", "/v1/tasks/job-123/result"]


def test_v4_downloads_middle_json_when_not_inlined() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/file_parse":
            return httpx.Response(200, json={"status": "completed", "files": [
                {"status": "completed", "output_files": {
                    "middle_json": {"file_id": "artifact-1"}
                }}
            ]})
        assert request.url.path == "/v1/files/artifact-1/content"
        return httpx.Response(200, json=MIDDLE_JSON_V4)

    assert extract(provider_with(handler)).document.page_count == 2


def test_v4_rejects_unrecognized_schema() -> None:
    invalid = {**MIDDLE_JSON_V4, "schema": "docvortex.model"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "completed", "files": [
            {"status": "completed", "content": {"middle_json": invalid}}
        ]})

    with pytest.raises(ProviderExecutionError):
        extract(provider_with(handler))
