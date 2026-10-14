"""Source-shaped regressions for the V2 audit findings."""

from __future__ import annotations

import asyncio
import io
import threading
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.datastructures import UploadFile
from starlette.requests import Request as StarletteRequest
from tests.unit.test_executor import MANIFEST, PAYLOAD, StubProvider

from acessilia_toolbox.api.rest import _read_upload, execute_capability, retrieve_artifact
from acessilia_toolbox.api.upload_limit import UploadLimitMiddleware
from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.errors import InvalidInputError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.normalization.builder import build_processing_manifest
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.normalization.table_ast import (
    linearize_table_for_text,
    rows_from_table_ast,
    table_ast_from_rows,
)
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers.docling_document import DoclingServeDocument
from acessilia_toolbox.providers.mineru_document import MineruDocument
from acessilia_toolbox.providers.storage import FilesystemArtifactStore
from acessilia_toolbox.providers.table_formats import docling_table, html_table


def test_body_references_preserve_nested_order_and_break_cycles():
    document = DoclingServeDocument(
        {
            "texts": [
                {"text": "before", "self_ref": "#/texts/0"},
                {"text": "after", "self_ref": "#/texts/1"},
            ],
            "pictures": [{"label": "picture", "self_ref": "#/pictures/0"}],
            "groups": [
                {
                    "self_ref": "#/groups/0",
                    "children": [{"$ref": "#/pictures/0"}, {"$ref": "#/groups/0"}],
                }
            ],
            "body": {
                "children": [{"$ref": "#/texts/0"}, {"$ref": "#/groups/0"}, {"$ref": "#/texts/1"}]
            },
        }
    )
    assert [item.self_ref for item, _ in document.iterate_items()] == [
        "#/texts/0",
        "#/groups/0",
        "#/pictures/0",
        "#/texts/1",
    ]


def test_manifest_keeps_list_and_picture_children_with_their_body_position(tmp_path):
    document = DoclingServeDocument(
        {
            "texts": [
                {"self_ref": "#/texts/0", "label": "text", "text": "Introduction"},
                {
                    "self_ref": "#/texts/1",
                    "label": "list_item",
                    "text": "First",
                    "parent": {"$ref": "#/groups/0"},
                },
                {
                    "self_ref": "#/texts/2",
                    "label": "list_item",
                    "text": "Second",
                    "parent": {"$ref": "#/groups/0"},
                },
                {"self_ref": "#/texts/3", "label": "text", "text": "After the chart"},
                {
                    "self_ref": "#/texts/4",
                    "label": "caption",
                    "text": "Chart caption",
                    "parent": {"$ref": "#/pictures/0"},
                },
            ],
            "groups": [
                {
                    "self_ref": "#/groups/0",
                    "label": "list",
                    "children": [{"$ref": "#/texts/1"}, {"$ref": "#/texts/2"}],
                }
            ],
            "tables": [{"self_ref": "#/tables/0", "label": "table"}],
            "pictures": [
                {
                    "self_ref": "#/pictures/0",
                    "label": "picture",
                    "children": [{"$ref": "#/texts/4"}],
                }
            ],
            "body": {
                "children": [
                    {"$ref": "#/texts/0"},
                    {"$ref": "#/groups/0"},
                    {"$ref": "#/tables/0"},
                    {"$ref": "#/pictures/0"},
                    {"$ref": "#/texts/3"},
                ]
            },
        }
    )
    source = tmp_path / "nested.pdf"
    source.write_bytes(b"%PDF-synthetic")
    now = datetime.now(UTC)
    extraction = ExtractionResult(
        document=document,
        backend="docling",
        version="test",
        configuration={},
        started_at=now,
        completed_at=now,
        duration_ms=0,
    )
    manifest = build_processing_manifest(source, extraction)
    elements = manifest.elements
    assert [element.type for element in elements] == [
        "paragraph",
        "group",
        "list_item",
        "list_item",
        "table",
        "picture",
        "caption",
        "paragraph",
    ]
    assert [element.hierarchy_level for element in elements] == [1, 1, 2, 2, 1, 1, 2, 1]
    assert elements[2].parent_id == elements[3].parent_id == elements[1].id
    assert elements[6].parent_id == elements[5].id
    assert elements[0].text == "Introduction"
    assert elements[-1].text == "After the chart"


def test_native_tables_preserve_headers_spans_and_blank_cells():
    table = docling_table(
        {
            "num_rows": 2,
            "num_cols": 3,
            "table_cells": [
                {
                    "text": "Region",
                    "start_row_offset_idx": 0,
                    "end_row_offset_idx": 1,
                    "start_col_offset_idx": 0,
                    "end_col_offset_idx": 2,
                    "column_header": True,
                },
                {
                    "text": "Value",
                    "start_row_offset_idx": 0,
                    "end_row_offset_idx": 1,
                    "start_col_offset_idx": 2,
                    "end_col_offset_idx": 3,
                    "column_header": True,
                },
                {
                    "text": "Recife",
                    "start_row_offset_idx": 1,
                    "end_row_offset_idx": 2,
                    "start_col_offset_idx": 2,
                    "end_col_offset_idx": 3,
                },
            ],
        }
    )
    assert table["header"][0]["cells"][0]["colspan"] == 2
    assert table["body"][0]["cells"] == [
        {"text": ""},
        {"text": ""},
        {"text": "Recife", "header": False, "scope": "none", "rowspan": 1, "colspan": 1},
    ]
    html = html_table(
        '<table><thead><tr><th colspan="2">Region</th></tr></thead>'
        "<tr><td></td><td>Recife &amp; Olinda</td></tr></table>"
    )
    assert html["header"][0]["cells"][0]["colspan"] == 2
    assert rows_from_table_ast(html)[1] == ["", "Recife & Olinda"]


def test_blank_cell_does_not_shift_header_association():
    rows = [["Name", "Age", "City"], ["Ana", "", "Recife"]]
    ast = table_ast_from_rows(rows)
    assert rows_from_table_ast(ast) == rows
    assert "Age: ; City: Recife" in linearize_table_for_text({"table_ast": ast})[0]


def test_mineru_native_table_and_formula_reach_manifest(tmp_path):
    document = MineruDocument(
        {
            "pdf_info": [
                {
                    "page_idx": 0,
                    "page_size": [100, 100],
                    "preproc_blocks": [
                        {
                            "type": "table",
                            "blocks": [
                                {
                                    "type": "table_body",
                                    "lines": [
                                        {
                                            "spans": [
                                                {
                                                    "type": "table",
                                                    "html": "<table><tr><td>42</td></tr></table>",
                                                }
                                            ]
                                        }
                                    ],
                                }
                            ],
                        },
                        {"type": "interline_equation", "latex": "x=1"},
                    ],
                }
            ]
        }
    )
    descriptor = ProviderDescriptor(id="stub", capabilities=["document.structure.extract"])
    extraction = StubProvider(descriptor).execute(
        "document.structure.extract", PAYLOAD, filename="doc.pdf", media_type="application/pdf"
    )
    extraction = replace(extraction, document=document)
    source = tmp_path / "doc.pdf"
    source.write_bytes(PAYLOAD)
    manifest = build_processing_manifest(source, extraction)
    assert rows_from_table_ast(manifest.elements[0].metadata["table_ast"]) == [["42"]]
    assert (manifest.elements[1].type, manifest.elements[1].text) == ("formula", "x=1")
    assert any(obligation.kind == "verbalize-formula" for obligation in manifest.obligations)


class MemoryCache:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value


def test_execution_cache_separates_source_context():
    descriptor = ProviderDescriptor(id="stub", capabilities=["document.structure.extract"])
    executor = CapabilityExecutor(
        CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)]),
        ProviderRegistry([descriptor]),
        lambda _: StubProvider(descriptor),
        cache=MemoryCache(),
    )

    def run(name, language):
        return executor.execute(
            "document.structure.extract",
            PAYLOAD,
            filename=name,
            media_type="application/pdf",
            language=language,
        )

    run("first.pdf", "pt-BR")
    second = run("second.pdf", "en-US")
    assert not second.provenance.cache_hit
    assert second.document["language"] == "en-US"
    assert second.document["source"]["filename"] == "second.pdf"
    assert run("second.pdf", "en-US").provenance.cache_hit


@pytest.mark.parametrize("identifier", ["../outside", "/tmp/outside", "sha256:../x", "xyz"])
def test_storage_rejects_external_identifiers(tmp_path, identifier):
    store = FilesystemArtifactStore(tmp_path / "store")
    with pytest.raises(InvalidInputError):
        store.get(identifier)


def test_storage_rejects_symlink_escape(tmp_path):
    store = FilesystemArtifactStore(tmp_path / "store")
    ref = store.put(b"safe")
    target = store._path(ref.artifact_id)
    target.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    target.symlink_to(outside)
    with pytest.raises(Exception, match="escapes storage root"):
        store.get(ref.artifact_id)


def test_unicode_download_header(tmp_path):
    store = FilesystemArtifactStore(tmp_path)
    ref = store.put(b"content", filename='report📄".pdf')
    runner = SimpleNamespace(retrieve_artifact=lambda _: (store.get(ref.artifact_id), ref))
    response = retrieve_artifact(ref.artifact_id, runner)
    assert response.headers["content-disposition"].endswith(
        "filename*=UTF-8''report%F0%9F%93%84%22.pdf"
    )


def test_upload_rejects_at_limit_plus_one(monkeypatch):
    monkeypatch.setattr("acessilia_toolbox.api.rest.MAX_UPLOAD_BYTES", 4)
    upload = UploadFile(io.BytesIO(b"123456789"), filename="doc.pdf")
    with pytest.raises(InvalidInputError):
        asyncio.run(_read_upload(upload))
    assert upload.file.tell() == 5


def test_ingress_limit_rejects_before_parser():
    app = FastAPI()
    app.add_middleware(UploadLimitMiddleware, max_bytes=4)

    @app.post("/")
    async def accept():
        raise AssertionError("oversized request reached application")

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://audit"
        ) as client:
            return await client.post("/", content=b"12345")

    assert asyncio.run(run()).status_code == 413


def test_slow_execution_does_not_block_event_loop():
    ready = threading.Event()

    class Runner:
        def execute(self, *args, **kwargs):
            assert ready.wait(timeout=2), "event loop blocked during provider execution"
            raise InvalidInputError("probe complete")

    async def run():
        async def signal():
            await asyncio.sleep(0.05)
            ready.set()

        pulse = asyncio.create_task(signal())
        try:
            with pytest.raises(InvalidInputError, match="probe complete"):
                await execute_capability(
                    "document.structure.extract",
                    Runner(),
                    StarletteRequest(
                        {
                            "type": "http",
                            "app": SimpleNamespace(
                                state=SimpleNamespace(execution_slots=threading.BoundedSemaphore(1))
                            ),
                        }
                    ),
                    file=UploadFile(io.BytesIO(PAYLOAD), filename="doc.pdf"),
                    artifact_id=None,
                    provider=None,
                    parameters=None,
                    language="pt-BR",
                    version=None,
                    no_cache=False,
                )
        finally:
            await pulse

    asyncio.run(run())


def test_dataset_sample_forwards_path_split():
    from acessilia_toolbox.api.rest import sample_dataset

    calls = []

    class Runner:
        def execute(self, capability, payload, **kwargs):
            calls.append((capability, kwargs["parameters"]))
            return SimpleNamespace(document=[])

    providers = ProviderRegistry(
        [ProviderDescriptor(id="dataset", capabilities=["dataset.describe"])]
    )
    sample_dataset("example", "test", Runner(), providers, revision=None, n=3)
    assert calls[-1] == (
        "dataset.sample",
        {"dataset_id": "example", "split": "test", "revision": None, "n": 3},
    )


def test_chunked_ingress_limit_cannot_be_bypassed():

    app = FastAPI()
    app.add_middleware(UploadLimitMiddleware, max_bytes=4)

    @app.post("/")
    async def accept(request: Request):
        await request.body()
        return {"accepted": True}

    async def chunks():
        yield b"123"
        yield b"456"

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://audit"
        ) as client:
            return await client.post("/", content=chunks())

    assert asyncio.run(run()).status_code == 413


def test_oversized_artifact_is_rejected_before_reading():
    from acessilia_toolbox.core.artifact import ArtifactRef

    class Store:
        def stat(self, identifier):
            return ArtifactRef(artifact_id=identifier, size=99)

        def get(self, identifier):
            raise AssertionError("oversized artifact was read into memory")

    descriptor = ProviderDescriptor(id="stub", capabilities=["document.structure.extract"])
    executor = CapabilityExecutor(
        CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)]),
        ProviderRegistry([descriptor]),
        lambda _: StubProvider(descriptor),
        store=Store(),
    )
    with pytest.raises(InvalidInputError, match="exceeds"):
        executor.retrieve_artifact("a" * 64, max_bytes=10)


def test_capacity_overload_rejects_work_and_keeps_health_responsive(monkeypatch):
    from acessilia_toolbox.api.app import create_app

    monkeypatch.setattr("acessilia_toolbox.api.auth.TOOLBOX_API_KEY", "")
    descriptor = ProviderDescriptor(id="stub", capabilities=["document.structure.extract"])
    app = create_app(
        CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)]),
        ProviderRegistry([descriptor]),
    )
    app.state.execution_slots = threading.BoundedSemaphore(1)
    entered, release = threading.Event(), threading.Event()

    class Runner:
        def execute(self, *args, **kwargs):
            entered.set()
            assert release.wait(3)
            raise InvalidInputError("finished probe")

    app.state.executor = Runner()

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://audit"
        ) as client:
            path = "/v1/capabilities/document.structure.extract:execute"
            first = asyncio.create_task(
                client.post(path, files={"file": ("doc.pdf", PAYLOAD, "application/pdf")})
            )
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                overloaded = await client.post(
                    path, files={"file": ("doc.pdf", PAYLOAD, "application/pdf")}
                )
                assert overloaded.status_code == 503
                assert (await client.get("/v1/health")).status_code == 200
            finally:
                release.set()
                await first

    asyncio.run(run())


def test_flattened_formula_keeps_existing_text_fallback():
    document = MineruDocument(
        {
            "pdf_info": [
                {"page_idx": 0, "preproc_blocks": [{"type": "interline_equation", "text": "x=1"}]}
            ]
        }
    )
    item, _ = next(document.iterate_items())
    assert item.label == "formula"
    assert item.text == "x=1"
