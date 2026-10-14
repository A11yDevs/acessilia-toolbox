"""Failure-path regressions from the follow-up repository audit."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pytest
from botocore.handlers import validate_ascii_metadata

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.errors import InvalidInputError
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.normalization.table_ast import linearize_table_for_text
from acessilia_toolbox.core.preprocessing.orient import detect_and_orient_image
from acessilia_toolbox.providers.dataset import DatasetAdapter
from acessilia_toolbox.providers.mineru_document import _ItemProxy
from acessilia_toolbox.providers.pymupdf_pdf import PyMuPDFProvider
from acessilia_toolbox.providers.storage import FilesystemArtifactStore, S3ArtifactStore
from acessilia_toolbox.providers.table_formats import html_table


class MemoryCache:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value, **kwargs):
        self.data[key] = value


def test_filesystem_repairs_partial_existing_object(tmp_path):
    store = FilesystemArtifactStore(tmp_path)
    payload = b"complete artifact bytes"
    target = store._path(fingerprint_bytes(payload))
    target.parent.mkdir(parents=True)
    target.write_bytes(payload[:3])
    ref = store.put(payload, filename="report.pdf")
    assert store.get(ref.artifact_id) == payload
    assert store.stat(ref.artifact_id).size == len(payload)


def test_atomic_publish_failure_leaves_old_object_and_no_temporary_file(tmp_path, monkeypatch):
    store = FilesystemArtifactStore(tmp_path)
    payload = b"complete payload"
    target = store._path(fingerprint_bytes(payload))
    original_replace = Path.replace

    def fail_publish(source, destination):
        if destination == target:
            assert source.read_bytes() == payload
            assert not target.exists()
            raise OSError("simulated failed publish")
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_publish)
    with pytest.raises(OSError):
        store.put(payload)
    assert not list(target.parent.iterdir())
    monkeypatch.setattr(Path, "replace", original_replace)
    ref = store.put(payload)
    assert store.get(ref.artifact_id) == payload


@pytest.mark.parametrize("filename", ["relatório.pdf", "文档.pdf", "100% ready.pdf"])
def test_s3_metadata_is_ascii_and_round_trips(filename):
    captured = {}

    def put_object(**kwargs):
        validate_ascii_metadata(kwargs)
        captured.update(kwargs)

    store = S3ArtifactStore.__new__(S3ArtifactStore)
    store.bucket = "test"
    store._client = SimpleNamespace(
        put_object=put_object,
        head_object=lambda **kw: {"Metadata": captured["Metadata"], "ContentLength": 4},
    )
    ref = store.put(b"data", filename=filename)
    assert store.stat(ref.artifact_id).filename == filename
    store._client.head_object = lambda **kw: {"Metadata": {"filename": "legacy.pdf"}}
    assert store.stat(ref.artifact_id).filename == "legacy.pdf"


@pytest.mark.parametrize(
    "markup",
    [
        "<table><tr><td>A<td>B</tr></table>",
        "<table><tr><td>A<td>B<tr><td>C<td>D</table>",
        "<table><tr><td>A</td><td>B</td></tr></table>",
        "<table><tr><td>A<table><tr><td>nested</td></tr></table><td>B</table>",
    ],
)
def test_optional_table_end_tags_and_nested_tables(markup):
    table = html_table(markup)
    assert table["body"][0]["cells"] == [
        {"text": "A", "header": False, "colspan": 1, "rowspan": 1},
        {"text": "B", "header": False, "colspan": 1, "rowspan": 1},
    ]
    if "<td>C" in markup:
        assert [c["text"] for c in table["body"][1]["cells"]] == ["C", "D"]


def test_merged_cell_labels_cover_all_logical_columns():
    table = {
        "header": [{"cells": [{"text": "AB", "colspan": 2}, {"text": "C"}]}],
        "body": [{"cells": [{"text": "A"}, {"text": "BC", "colspan": 2}]}],
    }
    assert linearize_table_for_text({"table_ast": table}) == ["Row 1: AB: A; AB / C: BC"]


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {"angle": 45},
        {"angle": True},
        {"confidence": "invalid"},
        {"confidence": float("nan")},
        {"confidence": 2},
        {"original_width": -1},
        {"oriented_height": "bad"},
    ],
)
def test_invalid_orientation_falls_back(response):
    adapter = SimpleNamespace(execute=lambda *a, **k: SimpleNamespace(document=response))
    payload, result = detect_and_orient_image(b"original", orient_provider=adapter)
    assert payload == b"original"
    assert not result.auto_rotated
    assert result.stage == "invalid_response"


@pytest.mark.parametrize(
    "name,value",
    [
        ("max_pages", -1),
        ("max_pages", 0),
        ("max_pages", 1001),
        ("max_pages", True),
        ("dpi", "invalid"),
        ("dpi", 0),
        ("dpi", 601),
        ("dpi", 1.5),
        ("page_number", False),
    ],
)
def test_pdf_invalid_parameters_are_client_errors(name, value):
    provider = PyMuPDFProvider.__new__(PyMuPDFProvider)
    with pytest.raises(InvalidInputError):
        method = provider._render if name == "page_number" else provider._split
        method(b"invalid PDF should never be opened", "doc.pdf", {name: value})


def test_openapi_exposes_bearer_only_for_protected_routes():
    schema = create_app().openapi()
    assert schema["components"]["securitySchemes"]["ApiKeyAuth"]["scheme"] == "bearer"
    assert schema["paths"]["/v1/capabilities/{capability_id}:execute"]["post"]["security"] == [
        {"ApiKeyAuth": []}
    ]
    assert not schema["paths"]["/v1/health"]["get"].get("security")


def make_dataset_adapter(tmp_path, provider_id="source"):
    adapter = DatasetAdapter.__new__(DatasetAdapter)
    adapter.descriptor = SimpleNamespace(id=provider_id)
    adapter._mirror = True
    adapter._mirror_refs = OrderedDict()
    adapter._cache = MemoryCache()
    adapter._store = FilesystemArtifactStore(tmp_path)
    return adapter


def test_dataset_metadata_keys_separate_split_revision_and_pagination(tmp_path):
    adapter = make_dataset_adapter(tmp_path)
    kw = {"dataset_id": "demo", "split": "train", "revision": "v1", "limit": 5, "offset": 0}
    keys = {adapter._meta_cache_key("list_items", kw)}
    for key, value in (("split", "test"), ("revision", "v2"), ("limit", 10), ("offset", 5)):
        keys.add(adapter._meta_cache_key("list_items", {**kw, key: value}))
    assert len(keys) == 5


def test_mirror_reuses_content_offline_and_separates_revision(tmp_path):
    adapter = make_dataset_adapter(tmp_path)
    calls = []

    def fetch(dataset, path, revision):
        calls.append(revision)
        return str(revision).encode(), "text/plain"

    adapter._provider = SimpleNamespace(get_artifact=fetch)
    assert adapter._get_artifact_maybe_mirrored("demo", "sample.txt", "v1")[0] == b"v1"
    assert adapter._get_artifact_maybe_mirrored("demo", "sample.txt", "v2")[0] == b"v2"
    adapter._provider.get_artifact = lambda *a: pytest.fail("mirror should avoid upstream")
    assert adapter._get_artifact_maybe_mirrored("demo", "sample.txt", "v1")[0] == b"v1"
    assert calls == ["v1", "v2"]
    adapter._provider.get_artifact = fetch
    adapter._store._path(fingerprint_bytes(b"v1")).unlink()
    assert adapter._get_artifact_maybe_mirrored("demo", "sample.txt", "v1")[0] == b"v1"
    assert calls == ["v1", "v2", "v1"]


@pytest.mark.parametrize(
    "lines,expected",
    [
        (["First line", "Second line"], "First line Second line"),
        (["state-of-", "the-art"], "state-of- the-art"),
        (["hello", "", "world"], "hello world"),
    ],
)
def test_mineru_lines_keep_word_boundaries(lines, expected):
    block = {"type": "text", "lines": [{"spans": [{"content": text}]} for text in lines]}
    assert _ItemProxy(block, 0, [100, 100]).text == expected


def test_orientation_cache_provenance_is_bound_to_original_source():
    import base64
    from datetime import UTC, datetime

    from acessilia_toolbox.core.capability import CapabilityRegistry
    from acessilia_toolbox.core.executor import CapabilityExecutor
    from acessilia_toolbox.core.normalization.extraction import ExtractionResult
    from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry

    providers = ProviderRegistry(
        [
            ProviderDescriptor(
                id="extractor", version="1", capabilities=["document.structure.extract"]
            ),
            ProviderDescriptor(id="orient", version="1", capabilities=["image.page.orient"]),
        ]
    )
    calls = []

    def orient(capability, payload, **kw):
        calls.append(payload)
        return SimpleNamespace(
            document={
                "angle": 90 if payload == b"rotated" else 0,
                "confidence": 0.9,
                "image_base64": base64.b64encode(b"upright").decode(),
            }
        )

    def extract(capability, payload, **kw):
        now = datetime.now(UTC)
        return ExtractionResult(
            document={"text": payload.decode()},
            backend="test",
            started_at=now,
            completed_at=now,
            duration_ms=1,
            version="1",
        )

    def factory(descriptor):
        return SimpleNamespace(
            execute=orient if descriptor.id == "orient" else extract,
            versions=lambda: {"provider": "1"},
        )

    executor = CapabilityExecutor(
        CapabilityRegistry.from_directory(Path("capabilities")),
        providers,
        factory,
        cache=MemoryCache(),
    )

    def run(payload):
        return executor.execute(
            "document.structure.extract",
            payload,
            filename="page.png",
            media_type="image/png",
            parameters={"auto_rotate": True},
        )

    first = run(b"rotated")
    second = run(b"upright")
    assert first.provenance.orientation["angle"] == 90
    assert second.provenance.orientation["angle"] == 0
    assert not second.provenance.cache_hit
    assert run(b"rotated").provenance.cache_hit
    assert run(b"upright").provenance.cache_hit
    assert calls == [b"rotated", b"upright"]


@pytest.mark.parametrize("splits", [("train", "test"), ("test", "train")])
def test_dataset_adapter_returns_requested_split(tmp_path, splits):
    from acessilia_toolbox.core.dataset import ItemSummary

    adapter = make_dataset_adapter(tmp_path)
    calls = []

    def list_items(dataset, split, revision, limit, offset):
        calls.append(split)
        return [ItemSummary(id=f"{split}-item", dataset=dataset, split=split)]

    adapter._provider = SimpleNamespace(list_items=list_items)
    for split in (*splits, *splits):
        result = adapter._dispatch(
            "dataset.list_items", dataset_id="demo", split=split, revision="v1", limit=10, offset=0
        )
        assert result[0]["id"] == f"{split}-item"
    assert calls == list(splits)
