"""MCP server behavior."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from tests.fixtures.documents import FakeDocument

from acessilia_toolbox.api.mcp import ToolboxMCPServer, create_mcp_app
from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import (
    ProviderDescriptor,
    ProviderHealth,
    ProviderRegistry,
)

pytestmark = pytest.mark.integration

CAPABILITY = "document.structure.extract"

MANIFEST = {
    "id": CAPABILITY,
    "version": 1,
    "description": "Extract document structure.",
    "input": {"schema": "artifact/document@1", "media_types": ["application/pdf"]},
    "output": {"schema": "artifact/structured-document@1"},
    "semantics": {
        "requires": ["document_available"],
        "produces": ["structured", "has_ast", "text_available"],
    },
    "providers": [{"id": "docling"}],
    "execution": {"deterministic": True, "cacheable": True, "timeout_hint_seconds": 600},
}


class StubProvider:
    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

    def execute(self, capability_id: str, payload: bytes, **_: Any) -> ExtractionResult:
        timestamp = datetime(2026, 9, 6, tzinfo=UTC)
        return ExtractionResult(
            document=FakeDocument(),
            backend="docling",
            started_at=timestamp,
            completed_at=timestamp,
            duration_ms=5,
            version="1.32.0",
            configuration={},
        )

    def versions(self) -> dict[str, str]:
        return {"provider": "1.32.0"}

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.descriptor.id, healthy=True, checked_at=datetime.now(UTC)
        )


@pytest.fixture
def mcp() -> ToolboxMCPServer:
    caps = CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)])
    providers = ProviderRegistry(
        [ProviderDescriptor.model_validate({"id": "docling", "capabilities": [CAPABILITY]})]
    )
    executor = CapabilityExecutor(caps, providers, lambda d: StubProvider(d))
    return create_mcp_app(caps, providers, executor)


def test_server_info_contains_metadata(mcp: ToolboxMCPServer) -> None:
    info = mcp.get_server_info()

    assert info["name"] == "acessilia-toolbox"
    assert info["version"]


def test_list_tools_derives_from_capability_manifests(mcp: ToolboxMCPServer) -> None:
    tools = mcp.list_tools()

    assert len(tools) == 1
    tool = tools[0]
    assert tool["name"] == "document_structure_extract"
    assert tool["description"]
    assert "inputSchema" in tool


def test_tool_has_required_input_properties(mcp: ToolboxMCPServer) -> None:
    tool = mcp.list_tools()[0]
    properties = tool["inputSchema"]["properties"]

    assert "file" in properties
    assert "media_type" in properties
    assert "provider" in properties


def test_list_resources_includes_planning_and_capabilities(mcp: ToolboxMCPServer) -> None:
    resources = mcp.list_resources()
    uris = {r["uri"] for r in resources}

    assert "acessilia://capabilities" in uris
    assert "acessilia://providers" in uris
    assert "acessilia://planning/domain" in uris
    assert "acessilia://planning/predicates" in uris
    assert "acessilia://planning/capabilities/document.structure.extract" in uris


def test_read_capabilities_resource_returns_json(mcp: ToolboxMCPServer) -> None:
    content = mcp.read_resource("acessilia://capabilities")

    data = content.strip()
    assert data.startswith("[")
    assert CAPABILITY in data


def test_read_planning_domain_returns_pddl(mcp: ToolboxMCPServer) -> None:
    content = mcp.read_resource("acessilia://planning/domain")

    assert "(define (domain acessilia-toolbox-fragment)" in content
    assert "(:action document-structure-extract" in content


def test_read_planning_predicates_returns_list(mcp: ToolboxMCPServer) -> None:
    content = mcp.read_resource("acessilia://planning/predicates")

    assert "document_available" in content
    assert "structured" in content


def test_read_capability_action_returns_pddl(mcp: ToolboxMCPServer) -> None:
    content = mcp.read_resource(
        "acessilia://planning/capabilities/document.structure.extract"
    )

    assert "(:action document-structure-extract" in content
    assert "(document_available)" in content
    assert "(structured)" in content


def test_unknown_resource_returns_error(mcp: ToolboxMCPServer) -> None:
    assert "Unknown resource" in mcp.read_resource("acessilia://unknown")


def test_providers_resource_redacts_credentials() -> None:
    caps = CapabilityRegistry([CapabilityManifest.model_validate(MANIFEST)])
    providers = ProviderRegistry(
        [
            ProviderDescriptor.model_validate(
                {
                    "id": "docling",
                    "capabilities": [CAPABILITY],
                    "config": {"secret_key": "SUPERSECRET"},
                }
            )
        ]
    )
    executor = CapabilityExecutor(caps, providers, lambda d: StubProvider(d))
    mcp = create_mcp_app(caps, providers, executor)
    content = mcp.read_resource("acessilia://providers")

    assert "SUPERSECRET" not in content
    assert "***" in content


def _mcp_with_opt_in_provider() -> ToolboxMCPServer:
    """docling is registered and bound; teleocr is bound but not registered
    (opt-in); mineru is registered and implements the capability unbound."""
    manifest = {**MANIFEST, "providers": [{"id": "docling"}, {"id": "teleocr"}]}
    caps = CapabilityRegistry([CapabilityManifest.model_validate(manifest)])
    providers = ProviderRegistry(
        [
            ProviderDescriptor.model_validate({"id": pid, "capabilities": [CAPABILITY]})
            for pid in ("docling", "mineru")
        ]
    )
    executor = CapabilityExecutor(caps, providers, lambda d: StubProvider(d))
    return create_mcp_app(caps, providers, executor)


def test_tool_offers_only_registered_providers() -> None:
    """An opt-in provider that is not registered cannot execute, so the tool
    does not offer it (the same rule as GET /v1/capabilities)."""
    [tool] = _mcp_with_opt_in_provider().list_tools()

    description = tool["inputSchema"]["properties"]["provider"]["description"]
    assert description == "Provider ID: docling, mineru"


def test_capabilities_resource_separates_registered_and_declared_providers() -> None:
    content = _mcp_with_opt_in_provider().read_resource("acessilia://capabilities")

    [entry] = json.loads(content)
    assert entry["providers"] == ["docling", "mineru"]
    assert entry["declared_providers"] == ["docling", "teleocr"]


def _mcp_with_two_versions(calls: list[dict[str, Any]]) -> ToolboxMCPServer:
    """The capability is published as version 1 and version 2."""

    class RecordingProvider(StubProvider):
        def execute(self, capability_id: str, payload: bytes, **kwargs: Any) -> ExtractionResult:
            calls.append(kwargs)
            return super().execute(capability_id, payload)

    version_2 = {**MANIFEST, "version": 2, "description": "Extract document structure, v2."}
    caps = CapabilityRegistry(
        [CapabilityManifest.model_validate(m) for m in (MANIFEST, version_2)]
    )
    providers = ProviderRegistry(
        [ProviderDescriptor.model_validate({"id": "docling", "capabilities": [CAPABILITY]})]
    )
    executor = CapabilityExecutor(caps, providers, lambda d: RecordingProvider(d))
    return create_mcp_app(caps, providers, executor)


def test_a_capability_in_two_versions_is_one_tool_running_the_latest(tmp_path: Any) -> None:
    calls: list[dict[str, Any]] = []
    mcp = _mcp_with_two_versions(calls)

    [tool] = mcp.list_tools()
    assert tool["description"].strip() == "Extract document structure, v2."

    document = tmp_path / "sample.pdf"
    document.write_bytes(b"%PDF-test")
    mcp.call_tool(tool["name"], {"file": str(document)})
    assert [call.get("capability_version") for call in calls] == [2]


def test_a_capability_in_two_versions_is_one_planning_action() -> None:
    mcp = _mcp_with_two_versions([])

    domain = mcp.read_resource("acessilia://planning/domain")
    assert domain.count("(:action document-structure-extract") == 1
    planning = [r["uri"] for r in mcp.list_resources() if "planning/capabilities/" in r["uri"]]
    assert planning == [f"acessilia://planning/capabilities/{CAPABILITY}"]


def test_the_catalogue_lists_every_version_and_flags_the_latest() -> None:
    content = _mcp_with_two_versions([]).read_resource("acessilia://capabilities")

    assert [(e["version"], e["latest"]) for e in json.loads(content)] == [(1, False), (2, True)]


def _recording_server(capability_id):
    from pathlib import Path
    from types import SimpleNamespace

    manifests = CapabilityRegistry.from_directory(
        Path(__file__).resolve().parents[2] / "capabilities"
    )
    caps = CapabilityRegistry([manifests.get(capability_id)])
    providers = ProviderRegistry(
        [
            ProviderDescriptor(id=provider, capabilities=[capability_id])
            for provider in ("provider-a", "provider-b")
        ]
    )
    calls = []

    def execute(capability, payload, **kwargs):
        calls.append({"capability": capability, "payload": payload, **kwargs})
        return SimpleNamespace(document={"executed": capability})

    return ToolboxMCPServer(caps, providers, SimpleNamespace(execute=execute)), calls


@pytest.mark.parametrize("capability", ["document.export.pdf_ua", "pdf.validate.pdf_ua"])
def test_tool_names_preserve_underscores_in_capability_ids(tmp_path, capability):
    server, calls = _recording_server(capability)
    [tool] = server.list_tools()
    file = tmp_path / "input.bin"
    file.write_bytes(b"input")

    response = server.call_tool(tool["name"], {"file": str(file)})

    assert calls[0]["capability"] == capability
    assert json.loads(response[0]["content"][0]["text"])["executed"] == capability


def test_ambiguous_tool_names_are_not_advertised():
    from types import SimpleNamespace

    from acessilia_toolbox.core.errors import ConfigurationError

    manifests = [
        CapabilityManifest.model_validate({**MANIFEST, "id": identifier})
        for identifier in ("document.export.pdf_ua", "document.export.pdf.ua")
    ]
    server = ToolboxMCPServer(CapabilityRegistry(manifests), ProviderRegistry(), SimpleNamespace())
    with pytest.raises(ConfigurationError, match="ambiguous"):
        server.list_tools()


@pytest.mark.parametrize("capability", ["math.recognize", "document.ocr.page"])
def test_nondeterministic_tools_advertise_and_forward_execution_controls(tmp_path, capability):
    server, calls = _recording_server(capability)
    [tool] = server.list_tools()
    properties = tool["inputSchema"]["properties"]
    assert {"provider", "parameters", "no_cache"} <= properties.keys()
    assert properties["provider"]["description"] == "Provider ID: provider-a, provider-b"
    file = tmp_path / "input.png"
    file.write_bytes(b"input")

    server.call_tool(
        tool["name"],
        {
            "file": str(file),
            "provider": "provider-b",
            "parameters": {"page_number": 2},
            "no_cache": True,
        },
    )

    assert calls[0]["provider_id"] == "provider-b"
    assert calls[0]["parameters"] == {"page_number": 2}
    assert calls[0]["no_cache"] is True


@pytest.mark.parametrize("parameters", ["{", "[]", "42", [], 1, True])
def test_invalid_parameters_return_an_error_without_executing(tmp_path, parameters):
    server, calls = _recording_server("document.export.docx")
    file = tmp_path / "input.json"
    file.write_text("{}")

    response = server.call_tool(
        "document_export_docx",
        {
            "file": str(file),
            "parameters": parameters,
        },
    )

    assert response[0]["content"][0]["text"].startswith("Error:")
    assert calls == []


@pytest.mark.parametrize(
    "parameters,expected",
    [
        (None, None),
        ("", None),
        ({"dpi": 150}, {"dpi": 150}),
        ('{"dpi": 150}', {"dpi": 150}),
    ],
)
def test_parameter_objects_and_legacy_json_strings_still_execute(tmp_path, parameters, expected):
    server, calls = _recording_server("document.export.docx")
    file = tmp_path / "input.json"
    file.write_text("{}")
    server.call_tool("document_export_docx", {"file": str(file), "parameters": parameters})
    assert calls[0]["parameters"] == expected
