"""Published dataset contracts cover serialized REST/MCP results, not provider internals."""

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
from fastapi.testclient import TestClient

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.api.mcp import ToolboxMCPServer
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.dataset_bindings import DATASET_REST_BINDINGS
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.core.schema_refs import schema_filename
from acessilia_toolbox.providers.dataset import DatasetAdapter
from acessilia_toolbox.providers.dataset_github import GitHubDatasetProvider
from acessilia_toolbox.providers.dataset_huggingface import HuggingFaceDatasetProvider
from acessilia_toolbox.providers.storage import FilesystemArtifactStore

ROOT = Path(__file__).resolve().parents[2]


def schema(ref: str) -> dict[str, Any]:
    return json.loads((ROOT / "schemas" / schema_filename(ref)).read_text())


def validate(ref: str, value: Any) -> None:
    jsonschema.Draft7Validator(schema(ref)).validate(value)


@pytest.fixture(params=["github", "huggingface"])
def gateway(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path) -> Iterator[SimpleNamespace]:
    """Real providers and adapter; only their upstream HTTP is simulated."""
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    kind = request.param
    descriptor = ProviderDescriptor(
        id=f"dataset-{kind}", version="1", transport="in_process",
        capabilities=list(DATASET_REST_BINDINGS), config={"mirror": True},
    )
    provider = (GitHubDatasetProvider if kind == "github" else HuggingFaceDatasetProvider)(
        descriptor
    )
    dataset = "acessilia-dataset" if kind == "github" else "dr-docbench"
    split = "input" if kind == "github" else "dev"
    provider._defs = {dataset: copy.deepcopy(provider._defs[dataset])}
    provider._defs[dataset]["splits"] = {
        split: provider._defs[dataset]["splits"][split]
    }
    state = {"empty": False, "listing_status": 200}

    def upstream(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if kind == "github":
            if path.endswith("/input/manifest.csv"):
                if state["listing_status"] != 200:
                    return httpx.Response(state["listing_status"])
                csv = "id,format\n" + ("" if state["empty"] else "one,pdf\ntwo,pdf\n")
                return httpx.Response(200, text=csv)
            return httpx.Response(200, content=b"artifact", headers={"content-type": "text/plain"})
        if "/tree/rev/" in path:
            if state["listing_status"] != 200:
                return httpx.Response(state["listing_status"])
            directory = path.split("/tree/rev/", 1)[1]
            entries = {
                "dev": [] if state["empty"] else ["subject"],
                "dev/subject": ["doc"],
                "dev/subject/doc/images": ["page_1.jpg", "page_2.jpg"],
            }
            assert directory in entries, path
            return httpx.Response(200, json=[{"path": name} for name in entries[directory]])
        return httpx.Response(200, content=b"artifact", headers={"content-type": "text/plain"})

    provider._http.close()
    provider._http = httpx.Client(transport=httpx.MockTransport(upstream))
    adapter = DatasetAdapter(descriptor, provider, store=FilesystemArtifactStore(tmp_path))
    capabilities = CapabilityRegistry.from_directory(ROOT / "capabilities")
    providers = ProviderRegistry([descriptor])
    runner = CapabilityExecutor(capabilities, providers, lambda _descriptor: adapter)
    app = create_app(capabilities, providers)
    app.state.executor = runner
    client = TestClient(app)
    try:
        yield SimpleNamespace(
            client=client, runner=runner, adapter=adapter, provider=provider,
            mcp=ToolboxMCPServer(capabilities, providers, runner), state=state,
            dataset=dataset, split=split, kind=kind,
            item="one" if kind == "github" else "doc_p1",
        )
    finally:
        client.close()
        provider._http.close()


def arguments(gateway: SimpleNamespace) -> dict[str, Any]:
    return {
        "dataset_id": gateway.dataset, "split": gateway.split,
        "item_id": gateway.item, "revision": "rev", "limit": 1, "offset": 0, "n": 2,
    }


def mcp_text(gateway: SimpleNamespace, capability: str, args: dict[str, Any]) -> str:
    result = gateway.mcp.call_tool(capability.replace(".", "_"), {"parameters": args})
    return result[0]["content"][0]["text"]


@pytest.mark.parametrize("capability", list(DATASET_REST_BINDINGS))
def test_each_provider_rest_and_mcp_result_conforms(
    gateway: SimpleNamespace, capability: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = DATASET_REST_BINDINGS[capability]
    args = arguments(gateway)
    path = binding.path.format(**args, path="input/file.txt")
    params = {"revision": "rev"}
    if capability == "dataset.list_items":
        params.update(limit="1", offset="1")
        args.update(offset=1)
    if capability == "dataset.sample":
        params["n"] = "2"
    if capability == "dataset.sync":
        params["split"] = gateway.split
    execute = gateway.runner.execute
    calls = []

    def recording(*positional: Any, **keywords: Any) -> Any:
        calls.append(keywords)
        return execute(*positional, **keywords)

    with monkeypatch.context() as context:
        context.setattr(gateway.runner, "execute", recording)
        result = gateway.client.request(binding.method, path, params=params)
    assert calls and all(call["capability_version"] == binding.capability_version for call in calls)
    assert result.status_code == 200, result.text
    if capability == "dataset.get_artifact":
        args["artifact_path"] = "input/file.txt"
    document = json.loads(mcp_text(gateway, capability, args))
    if capability == "dataset.get_artifact":
        assert result.content == b"artifact"
        assert bytes.fromhex(document["payload"]) == result.content
        assert document["media_type"] == "text/plain"
        assert result.headers["content-type"].split(";", 1)[0] == document["media_type"]
        return
    value = result.json()
    validate(binding.output_schema, value)
    manifest = gateway.runner._capabilities.get(capability, 1)
    validate(manifest.output.schema_ref, value)
    validate(binding.output_schema, document)
    validate(manifest.output.schema_ref, document)
    if capability == "dataset.describe":
        assert value["item_count"] is value["license"] is value["citation"] is None
        assert value["default_revision"] == "rev"
    elif capability == "dataset.list_items":
        assert len(value) == 1
        assert value[0]["id"] == ("two" if gateway.kind == "github" else "doc_p2")
        assert "artifact_count" in value[0] and "artifacts" not in value[0]
    elif capability in {"dataset.get_item", "dataset.sample"}:
        item = value if isinstance(value, dict) else value[0]
        assert item["artifacts"][0]["sha256"] is None
        assert "artifacts" in item and "artifact_count" not in item
    elif capability == "dataset.sync":
        assert value and all(entry["status"] == "mirrored" for entry in value)


@pytest.mark.parametrize("capability", ["dataset.list_items", "dataset.sample", "dataset.sync"])
def test_empty_collections_are_empty_arrays(gateway: SimpleNamespace, capability: str) -> None:
    gateway.state["empty"] = True
    binding = DATASET_REST_BINDINGS[capability]
    result = gateway.client.request(
        binding.method, binding.path.format(**arguments(gateway)),
        params={"revision": "rev", "split": gateway.split},
    )
    assert result.status_code == 200, result.text
    assert result.json() == []
    validate(binding.output_schema, result.json())
    document = json.loads(mcp_text(gateway, capability, arguments(gateway)))
    assert document == []
    validate(binding.output_schema, document)


def test_empty_discovery_and_split_lists(gateway: SimpleNamespace) -> None:
    gateway.provider._defs[gateway.dataset]["splits"] = {}
    splits = gateway.client.get(f"/v1/datasets/{gateway.dataset}/splits")
    assert splits.json() == []
    validate("artifact/dataset-splits@1", splits.json())
    assert json.loads(mcp_text(gateway, "dataset.list_splits", arguments(gateway))) == []
    gateway.provider._defs = {}
    datasets = gateway.client.get("/v1/datasets")
    assert datasets.json() == []
    validate("artifact/dataset-list@1", datasets.json())
    assert json.loads(mcp_text(gateway, "dataset.list", {})) == []


def test_sync_reports_disabled_mirroring_and_item_failure(
    gateway: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway.adapter._mirror = False
    binding = DATASET_REST_BINDINGS["dataset.sync"]
    path = binding.path.format(**arguments(gateway))
    params = {"revision": "rev", "split": gateway.split}
    result = gateway.client.post(path, params=params)
    assert result.json() == [{"status": "skipped", "reason": "mirroring is disabled"}]
    validate(binding.output_schema, result.json())
    document = json.loads(mcp_text(gateway, "dataset.sync", arguments(gateway)))
    assert document == result.json()
    validate(binding.output_schema, document)
    gateway.adapter._mirror = True

    def unavailable(*_args: Any) -> Any:
        raise RuntimeError("upstream temporarily unavailable")

    monkeypatch.setattr(gateway.provider, "get_item", unavailable)
    failures = gateway.client.post(path, params=params)
    assert len(failures.json()) == 2
    assert all(entry["status"] == "skipped" for entry in failures.json())
    validate(binding.output_schema, failures.json())
    document = json.loads(mcp_text(gateway, "dataset.sync", arguments(gateway)))
    assert document == failures.json()
    validate(binding.output_schema, document)


def test_sync_artifact_failures_remain_entries(
    gateway: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_artifact = gateway.provider.get_artifact

    def unavailable(dataset: str, path: str, revision: str | None = None) -> Any:
        if gateway.kind == "github" and path.endswith("manifest.csv"):
            return get_artifact(dataset, path, revision)
        raise RuntimeError("artifact unavailable")

    monkeypatch.setattr(gateway.provider, "get_artifact", unavailable)
    binding = DATASET_REST_BINDINGS["dataset.sync"]
    result = gateway.client.post(
        binding.path.format(**arguments(gateway)),
        params={"revision": "rev", "split": gateway.split},
    )
    assert result.status_code == 200
    validate(binding.output_schema, result.json())
    failed = [entry for entry in result.json() if entry["status"] == "skipped"]
    assert failed and all(entry["artifact"] and entry["reason"] for entry in failed)
    document = json.loads(mcp_text(gateway, "dataset.sync", arguments(gateway)))
    assert document == result.json()
    validate(binding.output_schema, document)


def test_route_errors_do_not_become_successful_empty_results(gateway: SimpleNamespace) -> None:
    base = f"/v1/datasets/{gateway.dataset}/splits/{gateway.split}/items"
    assert gateway.client.get(base, params={"limit": 0}).status_code == 422
    assert gateway.client.get(base + "/absent", params={"revision": "rev"}).status_code == 404
    assert mcp_text(gateway, "dataset.list_items", {
        **arguments(gateway), "limit": 0,
    }).startswith("Error:")
    assert mcp_text(gateway, "dataset.get_item", {
        **arguments(gateway), "item_id": "absent",
    }).startswith("Error:")


@pytest.mark.parametrize("status", [404, 500])
def test_upstream_listing_status_matches_documented_behavior(
    gateway: SimpleNamespace, status: int,
) -> None:
    gateway.state["listing_status"] = status
    binding = DATASET_REST_BINDINGS["dataset.list_items"]
    result = gateway.client.get(
        binding.path.format(**arguments(gateway)), params={"revision": "rev"},
    )
    text = mcp_text(gateway, "dataset.list_items", arguments(gateway))
    if status == 404:
        assert result.status_code == 200
        assert result.json() == json.loads(text) == []
        validate(binding.output_schema, result.json())
    else:
        assert result.status_code == 502
        assert text.startswith("Error:")


@pytest.mark.parametrize("ref,value", [
    ("dataset-list", {}),
    ("dataset-splits", [{"id": "split", "item_count": -1, "has_ground_truth": False}]),
    ("dataset-item-summary-list", [
        {"id": "one", "artifact_count": "bad", "has_annotations": True},
    ]),
    ("dataset-sample", [{"id": "one", "artifacts": [{"path": 12}]}]),
    ("dataset-sync-report", {}),
    ("dataset-sync-report", [{"status": "mirrored", "item": "one", "split": "input"}]),
    ("dataset-sync-report", [{"status": "skipped"}]),
    ("dataset-sync-report", [{"status": "unknown"}]),
])
def test_malformed_results_are_not_valid_contract_examples(ref: str, value: Any) -> None:
    with pytest.raises(jsonschema.ValidationError):
        validate(f"artifact/{ref}@1", value)


def test_object_contracts_keep_extension_fields_and_existing_constraints() -> None:
    info = {
        "id": "example", "name": "Example", "source": "https://example.invalid",
        "source_type": "github", "item_count": None, "license": None, "citation": None,
        "future": {"extension": True},
    }
    validate("artifact/dataset-info@1", info)
    with pytest.raises(jsonschema.ValidationError):
        validate("artifact/dataset-info@1", {**info, "item_count": -1})
    validate("artifact/dataset-artifact@1", {
        "path": "file.pdf", "sha256": None, "artifact_id": None, "future": True,
    })


def test_contract_and_openapi_bind_each_dedicated_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")
    capabilities = CapabilityRegistry.from_directory(ROOT / "capabilities")
    openapi = create_app(capabilities, ProviderRegistry([])).openapi()
    contract = json.loads((ROOT / "contract/toolbox-contract.json").read_text())
    published = {entry["capability"]: entry for entry in contract["dataset_routes"]}
    refs = {entry["ref"]: entry for entry in contract["schema_refs"]}
    documented = []
    for path, methods in openapi["paths"].items():
        if not path.startswith("/v1/datasets"):
            continue
        for method, operation in methods.items():
            binding = operation["x-toolbox-dataset-binding"]
            assert binding == published[binding["capability"]]
            assert (method.upper(), path) == (binding["method"], binding["path"])
            assert capabilities.get(binding["capability"], binding["capability_version"])
            reference = refs[binding["output_schema"]]
            assert reference["status"] == (
                "descriptor" if binding["capability"] == "dataset.get_artifact" else "published"
            )
            documented.append(binding["capability"])
    assert sorted(documented) == sorted(DATASET_REST_BINDINGS)
    items = openapi["paths"]["/v1/datasets/{dataset_id}/splits/{split}/items"]["get"]
    params = {param["name"]: param["schema"] for param in items["parameters"]}
    assert (params["limit"]["minimum"], params["limit"]["maximum"]) == (1, 1000)
    assert params["offset"]["minimum"] == 0


def test_generated_collections_are_fresh_and_valid() -> None:
    script = ROOT / "scripts/export_dataset_schemas.py"
    spec = importlib.util.spec_from_file_location("dataset_exporter", script)
    assert spec and spec.loader
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    for path, generated in exporter.build().items():
        assert path.read_text() == generated, path.name
        jsonschema.Draft7Validator.check_schema(json.loads(generated))
        jsonschema.Draft7Validator(json.loads(generated)).validate([])
    item = schema("artifact/dataset-item@1")
    artifact = schema("artifact/dataset-artifact@1")
    artifact.pop("$schema")
    artifact.pop("$id")
    assert item["definitions"]["DatasetArtifactRef"] == artifact
