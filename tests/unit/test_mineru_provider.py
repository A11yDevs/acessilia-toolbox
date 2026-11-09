"""mineru-api adapter behavior, exercised over a simulated transport."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import jsonschema
import pytest
from docstruct.tables.ast import rows_from_table_ast

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

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mineru"

# MinerU 4 protocol: ordered blocks, normalized bboxes and source-page geometry.
# The fixture is validated against the DocVortex 2.0 schema below.
MIDDLE_JSON_V4 = json.loads(
    (FIXTURES / "middle_json_v4.json").read_text(encoding="utf-8")
)
MIDDLE_JSON_V4_SCHEMA = json.loads(
    (FIXTURES / "middle-2.0.schema.json").read_text(encoding="utf-8")
)

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


def _v4_facade(payload=None):
    from acessilia_toolbox.providers.mineru_document import MineruDocument

    return MineruDocument(MIDDLE_JSON_V4 if payload is None else payload)


def _walk_blocks(blocks):
    for block in blocks:
        if not isinstance(block, dict):
            continue
        yield block
        content = block.get("content")
        if isinstance(content, list):
            yield from _walk_blocks(content)


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


# ── MinerU 4 protocol ────────────────────────────────────────────────


def test_v4_fixture_matches_docvortex_schema() -> None:
    jsonschema.validate(instance=MIDDLE_JSON_V4, schema=MIDDLE_JSON_V4_SCHEMA)
    for block in _walk_blocks(
        block for page in MIDDLE_JSON_V4["pages"] for block in page.get("blocks") or []
    ):
        bbox = block.get("bbox")
        if bbox is None:
            continue
        assert all(0.0 <= value <= 1.0 for value in bbox)
        assert bbox[0] < bbox[2]
        assert bbox[1] < bbox[3]


def test_v4_normalized_bbox_is_converted_with_source_page_geometry() -> None:
    document = _v4_facade()
    paragraph = next(
        item for item in document.texts if item.text.startswith("Use ")
    )
    assert paragraph.bbox.as_tuple() == pytest.approx((59.5, 126.3, 535.5, 210.5))
    assert paragraph.bbox.coord_origin.value == "TOPLEFT"
    provenance = paragraph.prov[0]
    assert provenance.page_no == 1
    assert provenance.bbox.as_tuple() == pytest.approx((59.5, 126.3, 535.5, 210.5))


def test_v4_partial_page_selection_keeps_source_page_numbers_and_sizes() -> None:
    payload = copy.deepcopy(MIDDLE_JSON_V4)
    payload["pages"][0]["page_idx"] = 3
    payload["pages"][1]["page_idx"] = 6
    payload["extensions"]["docvortex_layout"]["pages"] = [
        {"page_idx": 3, "width_pt": 612, "height_pt": 792},
        {"page_idx": 6, "width_pt": 595, "height_pt": 842},
    ]

    document = _v4_facade(payload)

    assert sorted(document.pages) == [4, 7]
    assert document.pages[4].size.width == 612
    assert document.pages[7].size.height == 842
    title = document.texts[0]
    assert title.prov[0].page_no == 4
    assert title.bbox.as_tuple() == pytest.approx((61.2, 15.84, 550.8, 47.52))
    second_page = next(item for item in document.texts if item.text == "Segunda página")
    assert second_page.prov[0].page_no == 7
    assert second_page.bbox.as_tuple() == pytest.approx((59.5, 84.2, 535.5, 126.3))


def test_v4_inline_spans_preserve_equation_code_and_hyperlink_text() -> None:
    document = _v4_facade()
    paragraph = next(
        item for item in document.texts if item.text.startswith("Use ")
    )
    assert paragraph.text == "Use x^2 and print(x) linked text"


def test_v4_table_equation_and_visual_annotations_are_read() -> None:
    document = _v4_facade()

    table = document.tables[0]
    assert table.html == "<table><tr><td>2025</td></tr></table>"
    assert table.table_ast is not None
    assert rows_from_table_ast(table.table_ast) == [["2025"]]

    formula = document.formulas[0]
    assert formula.latex == "E = mc^2"
    assert formula.text == "E = mc^2"

    texts = [item.text for item in document.texts]
    assert "Tabela 1" in texts
    assert "Figura 1" in texts
    assert "Nota de rodapé" in texts
    assert "print(1)" in texts


def test_v4_titles_lists_and_headings_map_to_document_types() -> None:
    document = _v4_facade()
    title = document.texts[0]
    assert (title.raw_label, title.label, title.level) == ("doc_title", "title", 1)
    heading = document.texts[1]
    assert (heading.raw_label, heading.label, heading.level) == (
        "paragraph_title",
        "heading",
        2,
    )
    list_items = [item for item in document.texts if item.label == "list_item"]
    assert [item.text for item in list_items] == ["Primeiro item", "Segundo item"]
    assert document.full_text.startswith("Relatório Anual\n\nResultados")


# ── provider adapter ─────────────────────────────────────────────────


def test_extraction_wraps_the_middle_json() -> None:
    extraction = extract(provider_with(parse_handler))

    assert extraction.backend == "mineru"
    assert extraction.version == "4.0.12"
    document = extraction.document
    assert document.page_count == 2
    assert len(document.texts) == 10


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
    assert len(extraction.document.texts) == 10


def test_http_error_maps_to_provider_execution_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": "bad file"})

    with pytest.raises(ProviderExecutionError) as excinfo:
        extract(provider_with(handler))
    assert excinfo.value.details.get("status_code") == 422
    assert "mineru-api rejected the document: HTTP 422 - bad file" in str(excinfo.value)


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
    assert {
        "title",
        "text",
        "list_block",
        "table",
        "formula",
        "embedded_image",
    } <= types
    assert regions[0]["page"] == 0
    title = next(region for region in regions if region["source_label"] == "title")
    assert title["raw_label"] == "doc_title"
    assert title["bbox"]["right"] == pytest.approx(535.5)
    assert extraction.configuration["region_count"] == len(regions)
    assert extraction.configuration["backend"] == "mineru-layout"


# ── ocr provider ─────────────────────────────────────────────────────


def test_ocr_provider_flattens_text_items() -> None:
    extraction = extract(provider_with(parse_handler, "mineru-ocr"), lang="ch")

    items = extraction.configuration["ocr_items"]
    assert len(items) == 10
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
        "Relatório Anual",
        "Resultados",
        "Use x^2 and print(x) linked text",
        "Primeiro item",
        "Segundo item",
        "Tabela 1",
        "Figura 1",
        "print(1)",
        "Nota de rodapé",
        "Segunda página",
    ]
    assert document.tables[0].html == "<table><tr><td>2025</td></tr></table>"
    assert document.formulas[0].latex == r"E = mc^2"
    assert document.pictures[0].label == "image"
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


def test_v4_manifest_uses_converted_geometry_and_full_content(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from acessilia_toolbox.core.normalization.builder import build_processing_manifest
    from acessilia_toolbox.core.normalization.extraction import ExtractionResult
    from acessilia_toolbox.providers.mineru_document import MineruDocument

    now = datetime.now(UTC)
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF-test")
    extraction = ExtractionResult(
        document=MineruDocument(MIDDLE_JSON_V4),
        backend="mineru",
        started_at=now,
        completed_at=now,
        duration_ms=0,
        version="4.0.12",
        configuration={"extractor": "mineru-v1"},
    )

    manifest = build_processing_manifest(source, extraction)

    assert manifest.title == "Relatório Anual"
    by_text = {element.text: element for element in manifest.elements if element.text}
    assert by_text["Relatório Anual"].type == "title"
    assert by_text["Resultados"].type == "heading"
    assert by_text["Resultados"].hierarchy_level == 2
    paragraph = by_text["Use x^2 and print(x) linked text"]
    assert paragraph.type == "paragraph"
    assert paragraph.provenance[0].bbox is not None
    assert paragraph.provenance[0].bbox.left == pytest.approx(59.5)
    assert paragraph.provenance[0].bbox.top == pytest.approx(126.3)
    assert paragraph.provenance[0].bbox.coord_origin == "TOPLEFT"
    table = next(element for element in manifest.elements if element.type == "table")
    assert rows_from_table_ast(table.metadata["table_ast"]) == [["2025"]]
    formula = next(element for element in manifest.elements if element.type == "formula")
    assert formula.text == "E = mc^2"
    assert all(element.type != "unknown" for element in manifest.elements)
