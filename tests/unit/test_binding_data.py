"""Binding data is published the same way by the contract, REST and MCP (#142).

Clients bind to a capability through its ``input``, ``output`` and
``execution`` blocks. The static contract (``scripts/export_contract.py``),
``GET /v1/capabilities`` and the MCP capabilities resource all publish them,
from one projection (``CapabilityManifest.binding_data``), so they must agree
for every capability version, compared by ``(id, version)``.
"""

from __future__ import annotations

import importlib.util
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.api.mcp import create_mcp_app
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderRegistry

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "export_contract.py"
BLOCKS = ("input", "output", "execution")


@lru_cache(maxsize=1)
def _exporter():
    spec = importlib.util.spec_from_file_location("export_contract", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _by_key(entries: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    return {(entry["id"], entry["version"]): {b: entry[b] for b in BLOCKS} for entry in entries}


@pytest.fixture(autouse=True)
def _unauthenticated_app(monkeypatch: pytest.MonkeyPatch) -> None:
    # The catalogue routes are read here without a key, as the integration
    # suite does (tests/integration/conftest.py).
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    return json.loads(_exporter().CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def capabilities() -> CapabilityRegistry:
    return CapabilityRegistry.from_directory(ROOT / "capabilities")


def test_the_contract_rest_and_mcp_publish_the_same_binding_data(
    contract: dict[str, Any], capabilities: CapabilityRegistry
) -> None:
    providers = ProviderRegistry([])
    rest = TestClient(create_app(capabilities, providers)).get("/v1/capabilities")
    assert rest.status_code == 200
    executor = CapabilityExecutor(capabilities, providers, lambda descriptor: None)
    mcp = json.loads(
        create_mcp_app(capabilities, providers, executor).read_resource("acessilia://capabilities")
    )

    published = _by_key(contract["capabilities"])
    assert len(published) == len(capabilities.manifests()) == 35
    assert _by_key(rest.json()) == published
    assert _by_key(mcp) == published


def test_every_capability_detail_carries_the_same_blocks(
    contract: dict[str, Any], capabilities: CapabilityRegistry
) -> None:
    client = TestClient(create_app(capabilities, ProviderRegistry([])))
    for (capability, version), blocks in _by_key(contract["capabilities"]).items():
        detail = client.get(f"/v1/capabilities/{capability}", params={"version": version})
        assert detail.status_code == 200, (capability, version)
        assert {b: detail.json()[b] for b in BLOCKS} == blocks, (capability, version)


def test_the_flat_catalogue_fields_keep_their_meaning(capabilities: CapabilityRegistry) -> None:
    """``media_types`` stays the input media types; pdf.render@2 shows why it matters."""
    client = TestClient(create_app(capabilities, ProviderRegistry([])))
    entry = next(
        e for e in client.get("/v1/capabilities").json()
        if (e["id"], e["version"]) == ("pdf.render", 2)
    )

    assert entry["media_types"] == entry["input"]["media_types"]
    assert entry["output"] == {
        "schema": "artifact/image@2",
        "media_types": ["image/png"],
        "by_reference": True,
    }
    assert entry["input_schema"] == entry["input"]["schema"]
    assert entry["output_schema"] == entry["output"]["schema"]
    assert entry["deterministic"] == entry["execution"]["deterministic"]
    assert entry["cacheable"] == entry["execution"]["cacheable"]


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (("dataset.sample", 1), {"deterministic": False, "idempotent": False, "cacheable": False}),
        (("dataset.sync", 1), {"deterministic": False, "idempotent": True, "cacheable": False}),
    ],
)
def test_idempotency_is_published_apart_from_determinism_and_caching(
    contract: dict[str, Any], key: tuple[str, int], expected: dict[str, bool]
) -> None:
    execution = _by_key(contract["capabilities"])[key]["execution"]
    assert {flag: execution[flag] for flag in expected} == expected


def _write(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


def _providers_config(tmp_path: Path, capability: str) -> Path:
    path = tmp_path / "providers-config.yaml"
    path.write_text(
        f"providers:\n  - id: stub\n    capabilities:\n      - {capability}\n",
        encoding="utf-8",
    )
    return path


def _every_interface(
    capabilities_dir: Path, providers_config: Path
) -> dict[str, dict[tuple[str, int], dict[str, Any]]]:
    """The binding blocks of each capability version, as each interface publishes them.

    ``detail`` asks for every version explicitly, so a route that picked the
    wrong version, or dropped a default, would differ from the contract.
    """
    capabilities = CapabilityRegistry.from_directory(capabilities_dir)
    providers = ProviderRegistry.from_file(providers_config)
    client = TestClient(create_app(capabilities, providers))
    executor = CapabilityExecutor(capabilities, providers, lambda descriptor: None)
    mcp = create_mcp_app(capabilities, providers, executor)
    contract = _exporter().build(capabilities_dir, providers_config, ROOT / "schemas")
    detail = {}
    for manifest in capabilities.manifests():
        response = client.get(
            f"/v1/capabilities/{manifest.id}", params={"version": manifest.version}
        )
        assert response.status_code == 200, manifest.key
        detail[(manifest.id, manifest.version)] = {b: response.json()[b] for b in BLOCKS}
    return {
        "contract": _by_key(contract["capabilities"]),
        "catalogue": _by_key(client.get("/v1/capabilities").json()),
        "detail": detail,
        "mcp": _by_key(json.loads(mcp.read_resource("acessilia://capabilities"))),
    }


def test_a_minimal_manifest_publishes_its_defaults_everywhere(tmp_path: Path) -> None:
    capabilities = tmp_path / "capabilities"
    _write(
        capabilities,
        "demo.minimal.yaml",
        "id: demo.minimal\nversion: 1\ndescription: Minimal.\n"
        "input:\n  schema: artifact/binary@1\noutput:\n  schema: artifact/binary@1\n",
    )

    published = _every_interface(capabilities, _providers_config(tmp_path, "demo.minimal"))

    expected = {
        "input": {"schema": "artifact/binary@1", "media_types": [], "by_reference": False},
        "output": {"schema": "artifact/binary@1", "media_types": [], "by_reference": False},
        "execution": {
            "deterministic": True,
            "idempotent": True,
            "cacheable": True,
            "timeout_hint_seconds": 120,
        },
    }
    for interface, blocks in published.items():
        assert blocks == {("demo.minimal", 1): expected}, interface


def test_two_versions_of_one_id_keep_their_own_metadata_everywhere(tmp_path: Path) -> None:
    capabilities = tmp_path / "capabilities"
    for version, idempotent, output in (
        (1, "true", "artifact/binary@1"),
        (2, "false", "artifact/image@2"),
    ):
        _write(
            capabilities,
            f"demo.versioned.v{version}.yaml",
            f"id: demo.versioned\nversion: {version}\ndescription: Versioned.\n"
            f"input:\n  schema: artifact/binary@1\noutput:\n  schema: {output}\n"
            f"execution:\n  idempotent: {idempotent}\n",
        )

    published = _every_interface(capabilities, _providers_config(tmp_path, "demo.versioned"))

    for interface, blocks in published.items():
        first, second = blocks[("demo.versioned", 1)], blocks[("demo.versioned", 2)]
        assert first["execution"]["idempotent"] is True, interface
        assert second["execution"]["idempotent"] is False, interface
        assert first["output"]["schema"] == "artifact/binary@1", interface
        assert second["output"]["schema"] == "artifact/image@2", interface
    assert published["catalogue"] == published["contract"] == published["mcp"]
    assert published["detail"] == published["contract"]


def test_the_contract_classifies_every_schema_reference(contract: dict[str, Any]) -> None:
    entries = {entry["ref"]: entry for entry in contract["schema_refs"]}
    used = {
        block["schema"]
        for capability in contract["capabilities"]
        for block in (capability["input"], capability["output"])
    }

    assert set(entries) == used
    for ref, entry in entries.items():
        if entry["status"] == "published":
            assert entry["file"] and (ROOT / entry["file"]).is_file(), ref
            assert entry["note"] is None, ref
        else:
            assert entry["status"] in {"descriptor", "external", "unpublished"}, ref
            assert entry["file"] is None and entry["note"], ref
    assert entries["artifact/structured-document@1"]["file"] == (
        "schemas/structured-document@1.json"
    )
    assert entries["artifact/binary@1"]["status"] == "descriptor"
    assert entries["artifact/pandoc-ast@1"]["status"] == "external"
    assert entries["artifact/reference@1"]["status"] == "unpublished"
