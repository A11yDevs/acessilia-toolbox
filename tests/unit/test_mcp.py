"""MCP server behavior."""

from __future__ import annotations

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
