"""MCP server exposing capabilities as tools and resources.

Follows the Model Context Protocol so AI agents discover and invoke toolbox
capabilities through the same contract that REST serves.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from acessilia_toolbox import __version__
from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.pddl import domain_fragment, predicates_list
from acessilia_toolbox.core.provider import ProviderRegistry

TOOLBOX_PREFIX = "acessilia://"
TOOL_CAPABILITY_PREFIX = "document.structure.extract"


def _tool_from_capability(manifest: dict[str, Any], registered: list[str]) -> dict[str, Any]:
    """Convert a capability manifest to an MCP tool definition.

    The ``provider`` argument lists the providers registered in this deployment,
    not the ones the manifest binds: an opt-in provider that is not registered
    (teleocr) cannot execute, so it is not offered.
    """
    action = manifest["id"].replace(".", "_")

    properties: dict[str, Any] = {
        "file": {
            "type": "string",
            "description": "File path to the document (or artifact_id for stored content)",
        },
        "media_type": {
            "type": "string",
            "description": "MIME type of the file (e.g., 'application/pdf')",
        },
    }
    if manifest["execution"].get("deterministic", True):
        properties["provider"] = {
            "type": "string",
            "description": "Provider ID: " + ", ".join(registered),
        }
        properties["parameters"] = {
            "type": "object",
            "description": "Provider-specific parameters as a JSON object",
        }
        properties["no_cache"] = {
            "type": "boolean",
            "description": "Skip execution cache and force re-processing",
        }

    return {
        "name": action,
        "description": manifest.get("description", ""),
        "inputSchema": {
            "type": "object",
            "properties": {k: v for k, v in properties.items() if v is not None},
        },
    }


class ToolboxMCPServer:
    """MCP server exposing capabilities as tools and resources."""

    def __init__(
        self,
        capabilities: CapabilityRegistry,
        providers: ProviderRegistry,
        executor: CapabilityExecutor,
    ) -> None:
        self._capabilities = capabilities
        self._providers = providers
        self._executor = executor

    def get_server_info(self) -> dict[str, Any]:
        """Return server metadata for MCP protocol negotiation."""
        return {
            "name": "acessilia-toolbox",
            "version": __version__,
            "description": "Deterministic, stateless capability layer for agentic systems",
        }

    def list_tools(self) -> list[dict[str, Any]]:
        tools = []
        # One tool per capability: call_tool executes the latest version.
        for manifest in self._capabilities.latest():
            tools.append(
                _tool_from_capability(
                    manifest.model_dump(mode="json", by_alias=True),
                    self._registered(manifest.id),
                )
            )
        return tools

    def _registered(self, capability_id: str) -> list[str]:
        """Providers registered here for a capability; what an agent can execute."""
        return sorted(d.id for d in self._providers.for_capability(capability_id))

    def _catalogue_entry(self, manifest: CapabilityManifest) -> dict[str, Any]:
        """A manifest as the catalogue publishes it, matching GET /v1/capabilities:
        ``providers`` lists the registered providers, ``declared_providers`` the
        ones the manifest binds."""
        entry = manifest.model_dump(mode="json", by_alias=True)
        entry["providers"] = self._registered(manifest.id)
        entry["declared_providers"] = [binding.id for binding in manifest.providers]
        return entry

    def call_tool(self, name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute a tool and return the results."""
        capability_id = name.replace("_", ".")
        manifest = self._capabilities.get(capability_id)

        # Dataset capabilities use parameters, not file uploads.
        if capability_id.startswith("dataset."):
            return self._call_dataset_tool(capability_id, manifest, arguments)

        file_path = arguments.get("file", "")
        media_type = arguments.get("media_type", "application/pdf")
        provider = arguments.get("provider")
        raw_params = arguments.get("parameters")

        if not os.path.exists(file_path):
            return [{"content": [{"type": "text", "text": f"File not found: {file_path}"}]}]

        parameters = None
        if isinstance(raw_params, str):
            parameters = json.loads(raw_params) if raw_params else None
        elif isinstance(raw_params, dict):
            parameters = raw_params

        try:
            result = self._executor.execute(
                manifest.id,
                Path(file_path).read_bytes(),
                filename=Path(file_path).name,
                media_type=media_type,
                provider_id=provider,
                parameters=parameters,
                no_cache=arguments.get("no_cache", False),
            )
            return [
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                result.document, ensure_ascii=False, indent=2
                            ),
                        }
                    ]
                }
            ]
        except Exception as exc:
            return [{"content": [{"type": "text", "text": f"Error: {exc}"}]}]

    def _call_dataset_tool(
        self,
        capability_id: str,
        manifest: Any,
        arguments: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Execute a dataset capability via parameters (no file upload)."""
        provider = arguments.get("provider", "dataset-github")
        params = {k: v for k, v in arguments.items() if k != "provider"}

        try:
            result = self._executor.execute(
                manifest.id,
                b"{}",
                filename="query.json",
                media_type="application/json",
                provider_id=provider,
                parameters=params,
            )
            return [
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                result.document, ensure_ascii=False, indent=2
                            ),
                        }
                    ]
                }
            ]
        except Exception as exc:
            return [{"content": [{"type": "text", "text": f"Error: {exc}"}]}]

    def list_resources(self) -> list[dict[str, Any]]:
        """Return MCP resources that agents can read."""
        resources = [
            {
                "uri": f"{TOOLBOX_PREFIX}capabilities",
                "name": "Available Capabilities",
                "description": "All capabilities exposed by the toolbox",
                "mimeType": "application/json",
            },
            {
                "uri": f"{TOOLBOX_PREFIX}providers",
                "name": "Registered Providers",
                "description": "All registered providers and their capabilities",
                "mimeType": "application/json",
            },
            {
                "uri": f"{TOOLBOX_PREFIX}planning/domain",
                "name": "PDDL Domain Fragment",
                "description": "PDDL domain fragment for planning integration",
                "mimeType": "application/vnd.pddl+text",
            },
            {
                "uri": f"{TOOLBOX_PREFIX}planning/predicates",
                "name": "PDDL Predicates",
                "description": "Available predicates for planning",
                "mimeType": "application/json",
            },
        ]

        for manifest in self._capabilities.latest():
            resources.append(
                {
                    "uri": f"{TOOLBOX_PREFIX}planning/capabilities/{manifest.id}",
                    "name": f"PDDL Action for {manifest.id}",
                    "description": f"PDDL action fragment for {manifest.id}",
                    "mimeType": "application/vnd.pddl+text",
                }
            )

        # Dataset resources
        resources.append(
            {
                "uri": f"{TOOLBOX_PREFIX}datasets",
                "name": "Available Datasets",
                "description": "All datasets available through the toolbox",
                "mimeType": "application/json",
            }
        )

        return resources

    def read_resource(self, uri: str) -> str:
        """Return the content of an MCP resource."""
        if uri == f"{TOOLBOX_PREFIX}capabilities":
            return json.dumps(
                [self._catalogue_entry(m) for m in self._capabilities.manifests()],
                indent=2,
                ensure_ascii=False,
            )

        if uri == f"{TOOLBOX_PREFIX}providers":
            return json.dumps(
                [d.public_payload() for d in self._providers.descriptors()],
                indent=2,
                ensure_ascii=False,
            )

        if uri == f"{TOOLBOX_PREFIX}planning/domain":
            return domain_fragment(self._capabilities.latest())

        if uri == f"{TOOLBOX_PREFIX}planning/predicates":
            return json.dumps(
                predicates_list(self._capabilities), indent=2, ensure_ascii=False
            )

        if uri.startswith(f"{TOOLBOX_PREFIX}planning/capabilities/"):
            capability_id = uri[len(f"{TOOLBOX_PREFIX}planning/capabilities/"):]
            manifest = self._capabilities.get(capability_id)
            from acessilia_toolbox.core.pddl import capability_action

            return capability_action(manifest)

        if uri == f"{TOOLBOX_PREFIX}datasets":
            result = self._executor.execute(
                "dataset.list",
                b"{}",
                filename="query.json",
                media_type="application/json",
                provider_id="dataset-github",
            )
            return json.dumps(result.document, indent=2, ensure_ascii=False)

        return f"Unknown resource: {uri}"


def create_mcp_app(
    capabilities: CapabilityRegistry,
    providers: ProviderRegistry,
    executor: CapabilityExecutor,
) -> ToolboxMCPServer:
    return ToolboxMCPServer(capabilities, providers, executor)
