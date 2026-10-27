"""Dataset MCP contracts using the real executor and dataset adapter."""

import json
from pathlib import Path

import pytest

from acessilia_toolbox.api.mcp import ToolboxMCPServer
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.dataset import DatasetInfo
from acessilia_toolbox.core.errors import DatasetNotFoundError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers.dataset import DatasetAdapter


class Source:
    def __init__(self, kind):
        self.kind = kind

    def list_datasets(self):
        return [self.describe(self.kind), self.describe("shared")]

    def describe(self, dataset_id, revision=None):
        if dataset_id not in (self.kind, "shared"):
            raise DatasetNotFoundError(f"unknown dataset {dataset_id!r}")
        return DatasetInfo(
            id=dataset_id,
            name=dataset_id,
            source="https://example.invalid/dataset",
            source_type=self.kind,
        )


def server(*kinds):
    capabilities = CapabilityRegistry.from_directory(Path("capabilities"))
    providers = ProviderRegistry(
        [
            ProviderDescriptor(
                id=f"dataset-{kind}", capabilities=["dataset.list", "dataset.describe"]
            )
            for kind in kinds
        ]
    )
    adapters = {
        d.id: DatasetAdapter(d, Source(d.id.removeprefix("dataset-")))
        for d in providers.descriptors()
    }
    executor = CapabilityExecutor(capabilities, providers, lambda d: adapters[d.id])
    return ToolboxMCPServer(capabilities, providers, executor)


def text(result):
    return result[0]["content"][0]["text"]


@pytest.mark.parametrize(
    "parameters",
    [
        {"parameters": {"dataset_id": "github"}},
        {"dataset_id": "github"},
        {"parameters": '{"dataset_id":"github"}'},
    ],
)
def test_dataset_describe_accepts_advertised_and_legacy_parameters(parameters):
    mcp = server("github")
    assert (
        json.loads(text(mcp.call_tool("dataset_describe", parameters)))["id"]
        == "github"
    )
    props = next(t for t in mcp.list_tools() if t["name"] == "dataset_describe")[
        "inputSchema"
    ]["properties"]
    assert props["parameters"]["type"] == "object"
    assert {"provider", "parameters", "no_cache"} == set(props)


@pytest.mark.parametrize("parameters", [[], 1, True, "[]", "{"])
def test_dataset_invalid_parameters_return_controlled_error(parameters):
    assert text(
        server("github").call_tool("dataset_describe", {"parameters": parameters})
    ).startswith("Error:")


def test_duplicate_dataset_parameters_are_rejected():
    result = server("github").call_tool(
        "dataset_describe",
        {
            "dataset_id": "github",
            "parameters": {"dataset_id": "shared"},
        },
    )
    assert "duplicate" in text(result)


def test_dataset_controls_are_forwarded_separately(monkeypatch):
    mcp = server("github")
    execute = mcp._executor.execute
    calls = []

    def recording(*args, **kwargs):
        calls.append(kwargs)
        return execute(*args, **kwargs)

    monkeypatch.setattr(mcp._executor, "execute", recording)
    result = mcp.call_tool(
        "dataset_describe",
        {
            "provider": "dataset-github",
            "parameters": {"dataset_id": "github"},
            "no_cache": True,
        },
    )
    assert json.loads(text(result))["id"] == "github"
    assert calls[0]["parameters"] == {"dataset_id": "github"}
    assert calls[0]["provider_id"] == "dataset-github"
    assert calls[0]["no_cache"] is True


def test_dataset_discovery_works_without_github():
    mcp = server("huggingface")
    assert (
        json.loads(
            text(
                mcp.call_tool(
                    "dataset_describe", {"parameters": {"dataset_id": "huggingface"}}
                )
            )
        )["id"]
        == "huggingface"
    )
    assert [d["id"] for d in json.loads(mcp.read_resource("acessilia://datasets"))] == [
        "huggingface",
        "shared",
    ]


def test_resource_aggregates_registered_providers_and_deduplicates():
    mcp = server("huggingface", "github")
    datasets = json.loads(mcp.read_resource("acessilia://datasets"))
    assert [d["id"] for d in datasets] == ["github", "shared", "huggingface"]
    assert datasets[1]["source_type"] == "github"
    assert "name one explicitly" in text(mcp.call_tool("dataset_list", {}))
    result = mcp.call_tool(
        "dataset_describe",
        {"provider": "dataset-huggingface", "dataset_id": "huggingface"},
    )
    assert json.loads(text(result))["id"] == "huggingface"


def test_dataset_resource_without_providers_is_empty():
    assert json.loads(server().read_resource("acessilia://datasets")) == []
