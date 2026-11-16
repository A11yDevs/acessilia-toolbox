"""Published layout variants cover the real provider/executor REST and MCP wire."""

from __future__ import annotations

import copy
import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import jsonschema
import pytest
from docstruct.manifest import structured_document_from_payload, validate_structured_document
from fastapi.testclient import TestClient

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.api.mcp import ToolboxMCPServer
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers.docling_layout import DoclingLayoutProvider
from acessilia_toolbox.providers.mineru_layout import MineruLayoutProvider

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas/layout-analysis@1.json").read_text("utf-8"))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
CAPABILITY = "document.layout.analyze"


@pytest.fixture(params=["docling-map", "docling-list", "mineru-v4"])
def gateway(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path) -> Iterator[SimpleNamespace]:
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")
    kind = request.param
    descriptor = ProviderDescriptor(
        id="docling-layout" if kind.startswith("docling") else "mineru-layout",
        version="1.32" if kind.startswith("docling") else "4.0.12",
        endpoint="http://layout.invalid", capabilities=[CAPABILITY],
    )
    if kind.startswith("docling"):
        raw = {
            "pages": {"2": {"width": 595, "height": 842}},
            "tables": [{
                "label": {"value": "table"}, "text": "Table", "confidence": 0.95,
                "prov": [{"page_no": 2, "bbox": {
                    "l": 50, "t": 500, "r": 500, "b": 200, "coord_origin": "BOTTOMLEFT",
                }}],
            }],
        }
        if kind == "docling-list":
            raw["pages"] = [{"page_number": 2, "size": {"width": 595, "height": 842}}]
        adapter = DoclingLayoutProvider(descriptor)
        endpoint = "/v1/convert/file"
    else:
        fixture = ROOT / "tests/fixtures/mineru/middle_json_v4.json"
        raw = json.loads(fixture.read_text("utf-8"))
        adapter = MineruLayoutProvider(descriptor)
        endpoint = "/v1/file_parse"
    state = {"empty": False, "status": 200}
    calls = []

    def upstream(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json={"version": descriptor.version})
        assert req.url.path == endpoint
        calls.append(req.url.path)
        if state["status"] != 200:
            return httpx.Response(state["status"], text="upstream unavailable")
        value = copy.deepcopy(raw)
        if kind.startswith("docling"):
            if state["empty"]:
                value["tables"] = []
            return httpx.Response(200, json={"document": {"json_content": value}})
        if state["empty"]:
            for page in value["pages"]:
                page["blocks"] = []
        return httpx.Response(200, json={"status": "completed", "files": [{
            "status": "completed", "content": {"middle_json": value},
        }]})

    monkeypatch.setattr(adapter, "_client", lambda timeout=None: httpx.Client(
        transport=httpx.MockTransport(upstream), base_url=descriptor.endpoint,
    ))
    capabilities = CapabilityRegistry.from_directory(ROOT / "capabilities")
    providers = ProviderRegistry([descriptor])
    executor = CapabilityExecutor(capabilities, providers, lambda _descriptor: adapter)
    app = create_app(capabilities, providers)
    app.state.executor = executor
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-test")
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, mcp=ToolboxMCPServer(capabilities, providers, executor),
            source=source, raw=raw, state=state, kind=kind, calls=calls,
        )


def invoke(gateway: SimpleNamespace, transport: str) -> dict[str, Any]:
    if transport == "rest":
        result = gateway.client.post(
            f"/v1/capabilities/{CAPABILITY}:execute",
            files={"file": ("sample.pdf", gateway.source.read_bytes(), "application/pdf")},
        )
        assert result.status_code == 200, result.text
        return result.json()["document"]
    result = gateway.mcp.call_tool("document_layout_analyze", {"file": str(gateway.source)})
    return json.loads(result[0]["content"][0]["text"])


@pytest.mark.parametrize("transport", ["rest", "mcp"])
@pytest.mark.parametrize("empty", [False, True])
def test_real_layout_providers_conform_through_both_interfaces(
    gateway: SimpleNamespace, transport: str, empty: bool,
) -> None:
    gateway.state["empty"] = empty
    document = invoke(gateway, transport)
    assert len(gateway.calls) == 1
    VALIDATOR.validate(document)
    assert isinstance(document["pages"], list)
    if gateway.kind.startswith("docling"):
        assert document["page_count"] == (0 if empty else 1)
        assert document["region_count"] == (0 if empty else 1)
        if not empty:
            page = document["pages"][0]
            assert page["page_number"] == 2
            region = page["regions"][0]
            assert region["type"] == region["label"] == "table"
            assert region["confidence"] == 0.95
            assert region["bbox"] == [50, 342, 500, 642]
    else:
        assert validate_structured_document(structured_document_from_payload(document)) == []
        config = document["extractor"]["configuration"]
        assert config["region_count"] == len(config["layout_regions"])
        assert (config["region_count"] == 0) is empty
        assert document["summary"]["page_count"] == len(document["pages"])
        assert document["pages"]
        if not empty:
            element = document["elements"][0]
            provenance = element["provenance"][0]
            assert provenance["bbox"]["coord_origin"] == "TOPLEFT"
            assert provenance["page_number"] == config["layout_regions"][0]["page"] + 1
            assert "confidence" not in config["layout_regions"][0]


@pytest.mark.parametrize("transport", ["rest", "mcp"])
def test_remote_failures_are_not_empty_successes(gateway: SimpleNamespace, transport: str) -> None:
    gateway.state["status"] = 500
    if transport == "rest":
        result = gateway.client.post(
            f"/v1/capabilities/{CAPABILITY}:execute",
            files={"file": ("sample.pdf", b"%PDF-test", "application/pdf")},
        )
        assert result.status_code == 502
    else:
        result = gateway.mcp.call_tool("document_layout_analyze", {"file": str(gateway.source)})
        assert result[0]["content"][0]["text"].startswith("Error:")


def test_missing_geometry_preserves_existing_origin_behavior(gateway: SimpleNamespace) -> None:
    if gateway.kind.startswith("docling"):
        gateway.raw["pages"] = {}
        assert invoke(gateway, "rest") == {"pages": [], "page_count": 0, "region_count": 0}
    else:
        del gateway.raw["extensions"]["docvortex_layout"]
        document = invoke(gateway, "rest")
        VALIDATOR.validate(document)
        assert document["pages"][0]["width"] is None
        assert document["elements"][0]["provenance"][0]["bbox"]["coord_origin"] == "UNKNOWN"


@pytest.mark.parametrize("value", [
    {}, {"pages": {}, "page_count": 0, "region_count": 0},
    {"pages": [], "page_count": -1, "region_count": 0},
    {"pages": [{"page_number": 0, "width": 0, "height": 0, "regions": []}],
     "page_count": 1, "region_count": 0},
])
def test_malformed_layout_is_not_a_valid_contract_example(value: dict[str, Any]) -> None:
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR.validate(value)


@pytest.mark.parametrize("missing", ["configuration", "layout_regions", "region_count", "backend"])
def test_canonical_manifest_requires_layout_metadata(missing: str) -> None:
    document = {
        "manifest_id": "m", "created_at": "2026-01-01T00:00:00Z",
        "source": {
            "document_id": "d", "filename": "a.pdf", "path": "/a.pdf",
            "media_type": "application/pdf", "byte_size": 1, "sha256": "0" * 64,
        },
        "extractor": {
            "version": "4.0", "started_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:00:01Z", "duration_ms": 1,
            "configuration": {"layout_regions": [], "region_count": 0, "backend": "mineru-layout"},
        },
        "title": "a", "language": "en", "pages": [], "elements": [],
        "summary": {
            "page_count": 0, "element_count": 0, "observation_count": 0, "obligation_count": 0,
        },
    }
    # The complete empty layout is valid; losing its metadata must not turn a
    # generic canonical manifest into a layout-analysis result.
    VALIDATOR.validate(document)
    if missing == "configuration":
        del document["extractor"]["configuration"]
    else:
        del document["extractor"]["configuration"][missing]
    canonical = json.loads((ROOT / "schemas/structured-document@1.json").read_text("utf-8"))
    jsonschema.Draft202012Validator(canonical).validate(document)
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR.validate(document)


@pytest.mark.parametrize("field,value", [
    ("bbox", [1, 2, 3]), ("bbox", [1, 2, 3, "bad"]),
    ("confidence", True), ("confidence", 1.1), ("type", "not-a-region"),
])
def test_region_constraints_are_enforced(field: str, value: Any) -> None:
    region = {
        "type": "table", "label": "table", "bbox": [1, 2, 3, 4],
        "confidence": None, "text": None, "page_number": 1,
    }
    validator = jsonschema.Draft202012Validator({
        **SCHEMA, "anyOf": [{"$ref": "#/$defs/LayoutRegion"}],
    })
    validator.validate(region)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**region, field: value})


def test_provider_variants_keep_required_fields_and_extension_policy(
    gateway: SimpleNamespace,
) -> None:
    document = invoke(gateway, "rest")
    if gateway.kind.startswith("docling"):
        region = document["pages"][0]["regions"][0]
        document["future_metadata"] = {"enabled": True}
        region["future_label"] = "extension"
        required_field = "bbox"
    else:
        region = document["extractor"]["configuration"]["layout_regions"][0]
        region["future_label"] = "extension"
        required_field = "source_label"
    VALIDATOR.validate(document)
    del region[required_field]
    with pytest.raises(jsonschema.ValidationError):
        VALIDATOR.validate(document)


def test_schema_is_fresh_self_contained_and_reuses_the_canonical_contract() -> None:
    script = ROOT / "scripts/export_layout_schema.py"
    spec = importlib.util.spec_from_file_location("layout_exporter", script)
    assert spec and spec.loader
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    assert (ROOT / "schemas/layout-analysis@1.json").read_text("utf-8") == exporter.render()
    VALIDATOR.check_schema(SCHEMA)
    canonical = json.loads((ROOT / "schemas/structured-document@1.json").read_text("utf-8"))
    for key, value in canonical.pop("$defs").items():
        assert SCHEMA["$defs"][key] == value
    for key in ("$schema", "$id", "description"):
        canonical.pop(key)
    assert SCHEMA["$defs"]["ProcessingManifest"] == canonical

    def check_refs(value: Any) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].startswith("#/$defs/")
            for nested in value.values():
                check_refs(nested)
        elif isinstance(value, list):
            for nested in value:
                check_refs(nested)

    check_refs(SCHEMA)
    contract = json.loads((ROOT / "contract/toolbox-contract.json").read_text("utf-8"))
    reference = next(r for r in contract["schema_refs"] if r["ref"] == "artifact/layout-analysis@1")
    assert reference == {
        "ref": "artifact/layout-analysis@1", "status": "published",
        "file": "schemas/layout-analysis@1.json", "note": None,
    }
