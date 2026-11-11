"""Contract tests for the pinned Dataset through the real GitHub provider.

GitHub HTTP is mocked; manifests and files come from the pinned Dataset
submodule, so the test checks the provider against the published revision
without network access.
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import subprocess
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
from fastapi.encoders import jsonable_encoder

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.api.mcp import ToolboxMCPServer
from acessilia_toolbox.api.rest import (
    get_dataset_artifact,
    get_dataset_item,
    list_dataset_items,
    list_dataset_splits,
)
from acessilia_toolbox.core.artifact import ExecutionCache
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import DatasetProviderError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers.dataset import DatasetAdapter
from acessilia_toolbox.providers.dataset_github import (
    DATASET_METADATA_SCHEMA,
    DATASET_REFERENCE_REVISION,
    REFERENCE_MANIFEST_COLUMNS,
    REFERENCE_MANIFEST_SCHEMA_PATH,
    GitHubDatasetProvider,
)
from acessilia_toolbox.providers.storage import FilesystemArtifactStore

REPO = Path(__file__).resolve().parents[2]
DATASET_FIXTURE = REPO / "tests" / "fixtures" / "dataset"
DATASET_ID = "acessilia-dataset"
REFERENCE_MANIFEST_SCHEMA = DATASET_FIXTURE / REFERENCE_MANIFEST_SCHEMA_PATH
DATASET_CAPABILITIES = [
    "dataset.list",
    "dataset.describe",
    "dataset.list_splits",
    "dataset.list_items",
    "dataset.get_item",
    "dataset.get_artifact",
    "dataset.sample",
    "dataset.sync",
]


class DictCache(ExecutionCache):
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.values.get(key)

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.values[key] = value


def _mock_github(
    files: dict[str, bytes | Path | None] | None = None,
    *,
    root: Path | None = DATASET_FIXTURE,
    resolved_sha: str = DATASET_REFERENCE_REVISION,
) -> tuple[httpx.Client, list[tuple[str, str, str]]]:
    requests: list[tuple[str, str, str]] = []

    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(
                200, json={"sha": resolved_sha}, request=request
            )
        parts = request.url.path.lstrip("/").split("/")
        if (
            request.url.host != "raw.githubusercontent.com"
            or len(parts) < 4
            or parts[0] != "A11yDevs"
            or parts[1] != "acessilia-dataset"
        ):
            return httpx.Response(404, request=request)
        revision = parts[2]
        path = "/".join(parts[3:])
        if request.method == "GET":
            requests.append((request.url.host, revision, path))
        content: bytes | None
        if files is not None and path in files:
            source = files[path]
            content = source.read_bytes() if isinstance(source, Path) else source
        elif root is not None:
            candidate = (root / path).resolve()
            content = (
                candidate.read_bytes()
                if candidate.is_relative_to(root.resolve()) and candidate.is_file()
                else None
            )
        else:
            content = None
        if content is None:
            return httpx.Response(404, request=request)
        media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return httpx.Response(
            200,
            content=content,
            headers={"content-type": media_type},
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(serve)), requests


def _csv_bytes(headers: list[str], rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def _reference_row(
    *,
    input_id: str = "001",
    task: str = "example_task",
    availability: str = "complete",
    file_path: str = "references/example.csv",
    schema_version: str = "1.2.0",
) -> dict[str, str]:
    row = dict.fromkeys(REFERENCE_MANIFEST_COLUMNS, "")
    row.update(
        {
            "input_id": input_id,
            "task": task,
            "availability": availability,
            "coverage_scope": "one fixture",
            "annotated_count": "1" if availability != "unannotated" else "0",
            "eligible_count": "1" if availability == "complete" else "",
            "coverage_unit": "fixture",
            "review_state": "not_recorded" if availability != "unannotated" else "not_applicable",
            "provenance": "synthetic contract test",
            "annotation_revision": "1" if availability != "unannotated" else "",
            "reference_type": (
                "ground_truth" if availability != "unannotated" else "expected_output"
            ),
            "format": "csv" if availability != "unannotated" else "txt",
            "schema_version": schema_version,
            "content_schema": (
                "references/schemas/example.schema.json"
                if availability != "unannotated"
                else ""
            ),
            "file_path": file_path if availability != "unannotated" else "",
            "record_selector": (
                json.dumps(
                    {
                        "type": "csv_filter",
                        "filters": {"input_id": input_id},
                        "count_mode": "rows",
                    }
                )
                if availability != "unannotated"
                else ""
            ),
        }
    )
    return row


@pytest.fixture
def real_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    assert DATASET_FIXTURE.is_dir(), "the pinned Dataset submodule is required"
    assert (
        subprocess.run(
            ["git", "-C", str(DATASET_FIXTURE), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == DATASET_REFERENCE_REVISION
    ), "Dataset fixture and provider revision must match"
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")
    from acessilia_toolbox.api import auth

    monkeypatch.setattr(auth, "TOOLBOX_API_KEY", "")
    descriptor = ProviderDescriptor(
        id="dataset-github",
        version="2.0",
        transport="in_process",
        capabilities=DATASET_CAPABILITIES,
        config={"mirror": True},
    )
    provider = GitHubDatasetProvider(descriptor)
    provider._http.close()
    provider._http, requests = _mock_github()
    cache = DictCache()
    store = FilesystemArtifactStore(tmp_path)
    adapter = DatasetAdapter(descriptor, provider, cache=cache, store=store)
    capabilities = CapabilityRegistry.from_directory(REPO / "capabilities")
    providers = ProviderRegistry([descriptor])
    executor = CapabilityExecutor(
        capabilities,
        providers,
        lambda _: adapter,
        cache=cache,
        store=store,
    )
    app = create_app(capabilities, providers, cache=cache, store=store)
    app.state.executor = executor
    yield {
        "provider": provider,
        "adapter": adapter,
        "requests": requests,
        "app": app,
        "mcp": ToolboxMCPServer(capabilities, providers, executor),
        "cache": cache,
        "executor": executor,
        "providers": providers,
    }
    provider._http.close()


def test_pinned_revision_paths_and_applicable_annotations(real_contract):
    provider = real_contract["provider"]
    split_schema = json.loads((REPO / "schemas/dataset-split@1.json").read_text())
    splits = {split.id: split for split in provider.list_splits(DATASET_ID)}
    for split in splits.values():
        jsonschema.validate(split.model_dump(mode="json"), split_schema)
    assert splits["intermediate"].deprecated is True
    assert splits["outputs"].deprecated is True

    summaries = provider.list_items(DATASET_ID, "input", limit=1000)
    assert len(summaries) == 36
    assert splits["input"].item_count == len(summaries)
    assert splits["input"].has_ground_truth == any(item.has_annotations for item in summaries)
    assert {"text/csv", "application/json"} <= set(splits["input"].artifact_types)
    assert all(isinstance(item.id, str) for item in summaries)
    assert "009" in {item.id for item in summaries}

    by_id = {item.id: item for item in summaries}
    assert by_id["001"].has_annotations is False
    assert by_id["009"].has_annotations is True
    assert by_id["009"].artifact_count == 2

    annotated = provider.get_item(DATASET_ID, "009", "input")
    assert annotated.revision == DATASET_REFERENCE_REVISION
    assert annotated.artifacts[0].path == "input/formula-images/simples_limpa.png"
    assert [ref.path for ref in annotated.annotations] == [
        "references/formula-images/ground_truth.csv"
    ]
    selectors = annotated.metadata["dataset_contract"]["references"]
    assert selectors[0]["record_selector"]["filters"] == {"manifest_id": "009"}
    slide = provider.get_item(DATASET_ID, "006", "input")
    assert all(
        reference["eligible_count"] is None
        for reference in slide.metadata["dataset_contract"]["references"]
        if reference["availability"] == "partial"
    )

    negative = provider.get_item(DATASET_ID, "033", "input")
    negative_reference = next(
        ref
        for ref in negative.metadata["dataset_contract"]["references"]
        if ref["task"] == "formula_image_recognition"
    )
    assert negative_reference["availability"] == "complete"
    assert negative.annotations

    for split_id in ("intermediate", "outputs"):
        for summary in provider.list_items(DATASET_ID, split_id, limit=1000):
            item = provider.get_item(DATASET_ID, summary.id, split_id)
            for artifact in [*item.artifacts, *item.annotations]:
                assert (DATASET_FIXTURE / artifact.path).is_file(), (
                    split_id,
                    summary.id,
                    artifact.path,
                )

    sample = {item.id: item for item in provider.sample(DATASET_ID, "input", n=36)}
    assert sample["009"].annotations == annotated.annotations
    for summary in summaries:
        item = provider.get_item(DATASET_ID, summary.id, "input")
        for artifact in [*item.artifacts, *item.annotations]:
            assert (DATASET_FIXTURE / artifact.path).is_file(), (
                summary.id,
                artifact.path,
            )
        for reference in item.metadata["dataset_contract"]["references"]:
            for key in ("path", "content_schema"):
                if reference[key]:
                    assert (DATASET_FIXTURE / reference[key]).is_file(), (
                        summary.id,
                        reference["task"],
                        key,
                        reference[key],
                    )


def test_public_v1_dataset_schemas_accept_additional_properties():
    item_schema = json.loads((REPO / "schemas/dataset-item@1.json").read_text())
    artifact_schema = json.loads((REPO / "schemas/dataset-artifact@1.json").read_text())
    artifact = {"path": "references/example.csv", "sha256": None, "extension": True}
    item = {"id": "001", "annotations": [artifact], "extension": True}

    jsonschema.validate(artifact, artifact_schema)
    jsonschema.validate(item, item_schema)


def test_real_provider_serializes_through_rest_and_mcp_with_revision(real_contract):
    app = real_contract["app"]
    splits = jsonable_encoder(
        list_dataset_splits(
            DATASET_ID,
            real_contract["executor"],
            real_contract["providers"],
            revision="main",
        )
    )
    split_schema = json.loads((REPO / "schemas/dataset-split@1.json").read_text())
    split_by_id = {split["id"]: split for split in splits}
    for split in splits:
        jsonschema.validate(split, split_schema)
    assert split_by_id["intermediate"]["deprecated"] is True
    assert split_by_id["outputs"]["deprecated"] is True

    summaries = jsonable_encoder(
        list_dataset_items(
            DATASET_ID,
            "input",
            real_contract["executor"],
            real_contract["providers"],
            revision="main",
            limit=2,
            offset=8,
        )
    )
    assert [item["id"] for item in summaries] == ["009", "010"]
    assert all(item["revision"] == DATASET_REFERENCE_REVISION for item in summaries)
    assert all(
        item["metadata"]["dataset_contract"]["revision"]
        == {"requested": "main", "effective": DATASET_REFERENCE_REVISION}
        for item in summaries
    )

    detail = jsonable_encoder(
        get_dataset_item(
            DATASET_ID,
            "input",
            "009",
            real_contract["executor"],
            real_contract["providers"],
            revision="main",
        )
    )
    item_schema = json.loads((REPO / "schemas/dataset-item@1.json").read_text())
    artifact_schema = json.loads(
        (REPO / "schemas/dataset-artifact@1.json").read_text()
    )
    jsonschema.validate(detail, item_schema)
    for artifact in [*detail["artifacts"], *detail["annotations"]]:
        jsonschema.validate(artifact, artifact_schema)
    assert detail["artifacts"][0]["sha256"] is None
    assert detail["annotations"][0]["artifact_id"] is None
    assert detail["metadata"]["dataset_contract"]["schema"] == DATASET_METADATA_SCHEMA

    sample = real_contract["adapter"].execute(
        "dataset.sample",
        b"{}",
        parameters={
            "dataset_id": DATASET_ID,
            "split": "input",
            "revision": "main",
            "n": 3,
        },
    ).document
    assert len(sample) == 3
    assert all(item["revision"] == DATASET_REFERENCE_REVISION for item in sample)
    assert all(
        item["metadata"]["dataset_contract"]["revision"]
        == {"requested": "main", "effective": DATASET_REFERENCE_REVISION}
        for item in sample
    )

    annotation_path = "references/formula-images/ground_truth.csv"
    download = get_dataset_artifact(
        DATASET_ID,
        "input",
        "009",
        annotation_path,
        real_contract["executor"],
        real_contract["providers"],
        revision="main",
    )
    assert download.status_code == 200
    assert download.headers["x-dataset-revision"] == DATASET_REFERENCE_REVISION
    assert download.headers["x-dataset-requested-revision"] == "main"
    assert download.body == (DATASET_FIXTURE / annotation_path).read_bytes()
    response_headers = app.openapi()["paths"][
        "/v1/datasets/{dataset_id}/splits/{split}/items/{item_id}/artifacts/{path}"
    ]["get"]["responses"]["200"]["headers"]
    assert {"X-Dataset-Revision", "X-Dataset-Requested-Revision"} <= set(
        response_headers
    )

    mcp = real_contract["mcp"]
    result = mcp.call_tool(
        "dataset_get_item",
        {
            "provider": "dataset-github",
            "parameters": {
                "dataset_id": DATASET_ID,
                "split": "input",
                "item_id": "009",
                "revision": "main",
            },
        },
    )
    assert not result[0]["content"][0]["text"].startswith("Error:")
    mcp_item = json.loads(result[0]["content"][0]["text"])
    jsonschema.validate(mcp_item, item_schema)
    assert mcp_item["metadata"]["dataset_contract"]["revision"] == {
        "requested": "main",
        "effective": DATASET_REFERENCE_REVISION,
    }
    assert all(
        revision == DATASET_REFERENCE_REVISION
        for _, revision, _ in real_contract["requests"]
    )


def test_adapter_warm_cache_and_sync_keep_sha_and_shared_selectors(real_contract, monkeypatch):
    provider = real_contract["provider"]
    adapter = real_contract["adapter"]

    first = adapter.execute(
        "dataset.list_items",
        b"{}",
        parameters={
            "dataset_id": DATASET_ID,
            "split": "input",
            "revision": "main",
            "limit": 2,
            "offset": 8,
        },
    ).document
    original = provider.list_items

    def cache_must_hit(*args, **kwargs):
        pytest.fail("the warm metadata cache must avoid another provider listing")

    monkeypatch.setattr(provider, "list_items", cache_must_hit)
    warm = adapter.execute(
        "dataset.list_items",
        b"{}",
        parameters={
            "dataset_id": DATASET_ID,
            "split": "input",
            "revision": "main",
            "limit": 2,
            "offset": 8,
        },
    ).document
    assert warm == first
    monkeypatch.setattr(provider, "list_items", original)

    def selected_page(dataset_id, split, revision, limit=100, offset=0):
        if offset:
            return []
        return original(dataset_id, split, revision, limit=2, offset=8)

    monkeypatch.setattr(provider, "list_items", selected_page)
    result = adapter.execute(
        "dataset.sync",
        b"{}",
        parameters={"dataset_id": DATASET_ID, "split": "input", "revision": "main"},
    ).document
    shared = [
        row
        for row in result
        if row.get("artifact") == "references/formula-images/ground_truth.csv"
    ]
    assert {row["item"] for row in shared} == {"009", "010"}
    assert all(row["task"] == "formula_image_recognition" for row in shared)
    assert {
        row["record_selector"]["filters"]["manifest_id"] for row in shared
    } == {"009", "010"}
    assert all(row["revision"] == DATASET_REFERENCE_REVISION for row in result)
    shared_downloads = [
        path
        for _, _, path in real_contract["requests"]
        if path == "references/formula-images/ground_truth.csv"
    ]
    assert len(shared_downloads) == 1


def test_sync_deduplicates_bytes_without_losing_logical_bindings(tmp_path: Path):
    from acessilia_toolbox.core.dataset import (
        DatasetArtifactRef,
        DatasetItem,
        ItemSummary,
    )

    revision = "c" * 40
    shared_path = "references/shared.csv"
    bindings = [
        {
            "task": task,
            "availability": "complete",
            "path": shared_path,
            "record_selector": {
                "type": "csv_filter",
                "filters": {"task_record": task},
                "count_mode": "rows",
            },
        }
        for task in ("task_a", "task_b")
    ]

    class SharedReferenceProvider:
        descriptor = ProviderDescriptor(
            id="dataset-github",
            version="2",
            capabilities=["dataset.sync"],
        )

        def resolve_revision(self, dataset_id, requested=None):
            return requested if requested and len(requested) == 40 else revision

        def list_items(self, dataset_id, split, requested, limit=100, offset=0):
            if offset:
                return []
            return [
                ItemSummary(
                    id="001",
                    dataset=dataset_id,
                    split=split,
                    revision=revision,
                    artifact_count=2,
                    has_annotations=True,
                )
            ]

        def get_item(self, dataset_id, item_id, split, requested):
            return DatasetItem(
                id=item_id,
                dataset=dataset_id,
                split=split,
                revision=revision,
                artifacts=[DatasetArtifactRef(path="input/source.pdf")],
                annotations=[DatasetArtifactRef(path=shared_path, media_type="text/csv")],
                metadata={"dataset_contract": {"references": bindings}},
            )

        def get_artifact(self, dataset_id, path, requested):
            downloads.append(path)
            return path.encode(), "text/csv" if path == shared_path else "application/pdf"

    downloads: list[str] = []
    descriptor = SharedReferenceProvider.descriptor.model_copy(
        update={"config": {"mirror": True}}
    )
    provider = SharedReferenceProvider()
    adapter = DatasetAdapter(
        descriptor,
        provider,
        cache=DictCache(),
        store=FilesystemArtifactStore(tmp_path),
    )
    result = adapter.execute(
        "dataset.sync",
        b"{}",
        parameters={
            "dataset_id": DATASET_ID,
            "split": "input",
            "revision": "main",
        },
    ).document

    logical_rows = [row for row in result if row.get("artifact") == shared_path]
    assert len(logical_rows) == 2
    assert {row["task"] for row in logical_rows} == {"task_a", "task_b"}
    assert {row["record_selector"]["filters"]["task_record"] for row in logical_rows} == {
        "task_a",
        "task_b",
    }
    assert downloads.count(shared_path) == 1
    assert all(row["revision"] == revision for row in result)


def test_revision_layout_matrix_legacy_transition_and_new_fail_closed():
    legacy_sha = "a" * 40
    source = {
        "input/manifest.csv": (
            b"id,original_filename,format,media_type\n"
            b"001,legacy.pdf,pdf,application/pdf\n"
        ),
        "intermediate/manifest.csv": (
            b"input_id,intermediate_type,file_path,status\n"
            b"001,canonical-document,canonical-document/001.json,completed\n"
        ),
        "outputs/manifest.csv": (
            b"input_id,format,file_path,status\n001,txt,outputs/txt/001.txt,\n"
        ),
        "input/001.pdf": b"pdf",
        "intermediate/canonical-document/001.json": b"{}",
    }
    client, _ = _mock_github(source, root=None)
    descriptor = ProviderDescriptor(id="dataset-github", capabilities=DATASET_CAPABILITIES)
    provider = GitHubDatasetProvider(descriptor)
    provider._http.close()
    provider._http = client
    try:
        legacy_info = provider.describe(DATASET_ID, legacy_sha)
        assert legacy_info.default_revision == DATASET_REFERENCE_REVISION
        assert legacy_sha in legacy_info.revisions
        split_info = {
            split.id: split for split in provider.list_splits(DATASET_ID, legacy_sha)
        }
        splits = set(split_info)
        assert splits == {"input", "intermediate", "outputs"}
        assert split_info["intermediate"].deprecated is True
        assert split_info["outputs"].deprecated is True
        legacy_item = provider.get_item(DATASET_ID, "001", "input", legacy_sha)
        assert legacy_item.artifacts[0].path == "input/001.pdf"
        output = provider.get_item(DATASET_ID, "001", "outputs", legacy_sha)
        assert output.artifacts == []
        assert provider.list_items(DATASET_ID, "input", legacy_sha)[0].artifact_count == 1
    finally:
        provider._http.close()

    stable_provider = GitHubDatasetProvider(descriptor)
    stable_provider._http.close()
    stable_provider._http, _ = _mock_github(root=DATASET_FIXTURE)
    try:
        old_info = stable_provider.describe(DATASET_ID, legacy_sha)
        assert old_info.default_revision == DATASET_REFERENCE_REVISION
        assert stable_provider.describe(DATASET_ID).default_revision == (
            DATASET_REFERENCE_REVISION
        )
    finally:
        stable_provider._http.close()

    transition_sha = "b" * 40
    transition_files = {
        "input/manifest.csv": (
            b"id,input_path,format,media_type\n"
            b"001,input/001.pdf,pdf,application/pdf\n"
        ),
        "references/manifest.csv": _csv_bytes(
            list(REFERENCE_MANIFEST_COLUMNS),
            [_reference_row(availability="unannotated")],
        ),
        REFERENCE_MANIFEST_SCHEMA_PATH: REFERENCE_MANIFEST_SCHEMA,
        "intermediate/manifest.csv": source["intermediate/manifest.csv"],
        "outputs/manifest.csv": source["outputs/manifest.csv"],
        "input/001.pdf": source["input/001.pdf"],
    }
    client, _ = _mock_github(transition_files, root=None, resolved_sha=transition_sha)
    provider = GitHubDatasetProvider(descriptor)
    provider._http.close()
    provider._http = client
    try:
        transition_splits = {
            split.id: split for split in provider.list_splits(DATASET_ID, transition_sha)
        }
        assert set(transition_splits) == {
            "input",
            "intermediate",
            "outputs",
        }
        assert transition_splits["intermediate"].deprecated is True
        assert transition_splits["outputs"].deprecated is True
        assert transition_splits["input"].has_ground_truth is False
        item = provider.get_item(DATASET_ID, "001", "input", transition_sha)
        assert item.annotations == []
        assert item.metadata["dataset_contract"]["layout"] == "transition-v1.2"
        assert item.metadata["dataset_contract"]["unannotated_tasks"] == [
            "example_task"
        ]
    finally:
        provider._http.close()


@pytest.mark.parametrize(
    "files,reason",
    [
        (
            {
                "input/manifest.csv": b"id,input_path\n001,input/001.pdf\n",
            },
            "new input manifest requires references/manifest.csv",
        ),
        (
            {
                "input/manifest.csv": b"id,input_path\n001,input/001.pdf\n",
                "references/manifest.csv": _csv_bytes(
                    list(REFERENCE_MANIFEST_COLUMNS),
                    [_reference_row(schema_version="9.9.9")],
                ),
                REFERENCE_MANIFEST_SCHEMA_PATH: REFERENCE_MANIFEST_SCHEMA,
                "intermediate/manifest.csv": b"input_id,file_path\n001,legacy.json\n",
            },
            "unsupported references manifest schema version",
        ),
        ({}, "required dataset manifest is missing: input/manifest.csv"),
    ],
)
def test_required_and_incompatible_manifests_fail_closed(files, reason):
    client, _ = _mock_github(files, root=None)
    provider = GitHubDatasetProvider(
        ProviderDescriptor(id="dataset-github", capabilities=DATASET_CAPABILITIES)
    )
    provider._http.close()
    provider._http = client
    try:
        with pytest.raises(DatasetProviderError, match=reason):
            provider.list_splits(DATASET_ID)
    finally:
        provider._http.close()


def test_legacy_revision_preserves_numbered_paths_and_formula_references(real_contract):
    revision = "9ed07b28aeddb69721228f2dd66de6f9794c0001"
    manifests = (
        "input/manifest.csv",
        "input/formula-images/ground_truth.csv",
        "intermediate/manifest.csv",
        "outputs/manifest.csv",
    )
    files = {
        path: subprocess.check_output(
            ["git", "-C", str(DATASET_FIXTURE), "show", f"{revision}:{path}"]
        )
        for path in (
            *manifests,
            *(f"input/{number:03d}.{'jpeg' if number == 5 else 'pdf'}" for number in range(1, 9)),
            "input/formula-images/simples_limpa.png",
            "input/formula-images/texto_paragrafo.png",
        )
    }
    provider = real_contract["provider"]
    provider._http.close()
    provider._http, _ = _mock_github(files, root=None)

    for number in range(1, 9):
        input_id = f"{number:03d}"
        suffix = "jpeg" if input_id == "005" else "pdf"
        item = provider.get_item(DATASET_ID, input_id, "input", revision)
        assert item.artifacts[0].path == f"input/{input_id}.{suffix}"
        assert not item.annotations
    for input_id in ("009", "033"):
        item = provider.get_item(DATASET_ID, input_id, "input", revision)
        assert [annotation.path for annotation in item.annotations] == [manifests[1]]


def test_published_legacy_revision_requires_formula_reference_manifest(real_contract):
    revision = "9ed07b28aeddb69721228f2dd66de6f9794c0001"
    files = {
        path: subprocess.check_output(
            ["git", "-C", str(DATASET_FIXTURE), "show", f"{revision}:{path}"]
        )
        for path in ("input/manifest.csv", "input/formula-images/simples_limpa.png")
    }
    provider = real_contract["provider"]
    provider._http.close()
    provider._http, _ = _mock_github(files, root=None)

    with pytest.raises(DatasetProviderError, match="required dataset manifest is missing") as error:
        provider.get_item(DATASET_ID, "009", "input", revision)

    assert error.value.details["reason"] == "required_manifest_missing"
    assert error.value.details["path"] == "input/formula-images/ground_truth.csv"
    assert error.value.details["revision"] == revision


@pytest.mark.parametrize(
    ("missing_file", "annotated_count", "filter_id", "record_ids", "reason"),
    [
        ("references/example.csv", "1", "001", "001", "required reference file"),
        ("references/schemas/example.schema.json", "1", "001", "001", "required reference file"),
        (None, "99", "001", "001", "inconsistent coverage counts"),
        (None, "1", "002", "002", "invalid record selector"),
        (None, "1", "001", "002", "selector resolved to 0 records"),
        (None, "1", "001", "001\n001", "selector resolved to 2 records"),
    ],
)
def test_broken_references_are_not_advertised(
    missing_file, annotated_count, filter_id, record_ids, reason
):
    row = _reference_row()
    row["annotated_count"] = annotated_count
    row["record_selector"] = json.dumps({
        "type": "csv_filter",
        "filters": {"manifest_id": filter_id},
        "count_mode": "rows",
    })
    files = {
        "input/manifest.csv": b"id,input_path\n001,input/001.pdf\n",
        "input/001.pdf": b"pdf",
        "references/manifest.csv": _csv_bytes(list(REFERENCE_MANIFEST_COLUMNS), [row]),
        REFERENCE_MANIFEST_SCHEMA_PATH: REFERENCE_MANIFEST_SCHEMA,
        row["file_path"]: f"manifest_id\n{record_ids}\n".encode(),
        row["content_schema"]: b"{}",
    }
    if missing_file is not None:
        del files[missing_file]
    provider = GitHubDatasetProvider(
        ProviderDescriptor(id="dataset-github", capabilities=DATASET_CAPABILITIES)
    )
    provider._http.close()
    provider._http, _ = _mock_github(files, root=None)
    try:
        with pytest.raises(DatasetProviderError, match=reason):
            provider.get_item(DATASET_ID, "001", "input")
        with pytest.raises(DatasetProviderError, match=reason):
            provider.list_items(DATASET_ID, "input")
        with pytest.raises(DatasetProviderError, match=reason):
            provider.list_splits(DATASET_ID)
    finally:
        provider._http.close()


@pytest.mark.parametrize(
    ("updates", "field"),
    [
        ({"coverage_unit": "unknown_unit"}, "coverage_unit"),
        ({"review_state": "reviewed", "reviewer": "", "reviewed_at": ""}, "reviewer"),
        ({"review_state": "reviewed", "reviewer": "Reviewer", "reviewed_at": ""}, "reviewed_at"),
    ],
)
def test_reference_metadata_must_match_the_published_schema(real_contract, updates, field):
    with (DATASET_FIXTURE / "references/manifest.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    next(row for row in rows if row["input_id"] == "009").update(updates)
    provider = real_contract["provider"]
    provider._http.close()
    provider._http, _ = _mock_github({
        "references/manifest.csv": _csv_bytes(list(REFERENCE_MANIFEST_COLUMNS), rows),
    })

    with pytest.raises(DatasetProviderError, match=f"invalid {field}"):
        provider.get_item(DATASET_ID, "009", "input")
    with pytest.raises(DatasetProviderError, match=f"invalid {field}"):
        provider.list_splits(DATASET_ID)


def test_reference_selector_must_match_its_task_even_with_same_count(real_contract):
    with (DATASET_FIXTURE / "references/manifest.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    text = next(row for row in rows if row["task"] == "math_text_quality")
    structure = next(row for row in rows if row["task"] == "math_document_structure")
    assert text["annotated_count"] == structure["annotated_count"]
    text["record_selector"] = structure["record_selector"]

    provider = real_contract["provider"]
    provider._http.close()
    provider._http, _ = _mock_github({
        "references/manifest.csv": _csv_bytes(list(REFERENCE_MANIFEST_COLUMNS), rows),
    })
    mismatch = "selector does not match task 'math_text_quality'"
    with pytest.raises(DatasetProviderError, match=mismatch):
        provider.get_item(DATASET_ID, "036", "input")
    with pytest.raises(DatasetProviderError, match=mismatch):
        provider.list_items(DATASET_ID, "input")


@pytest.mark.parametrize("reference_format", ["csv", "json", "schema"])
def test_invalid_reference_content_is_not_advertised(real_contract, reference_format):
    item_id = "009"
    path = "references/formula-images/ground_truth.csv"
    reason = "invalid_reference"
    if reference_format == "csv":
        with (DATASET_FIXTURE / path).open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        next(row for row in rows if row["manifest_id"] == item_id)["expected_latex"] = ""
        broken = _csv_bytes(list(rows[0]), rows)
    elif reference_format == "json":
        item_id = "036"
        path = "references/math/036/expectations.json"
        document = json.loads((DATASET_FIXTURE / path).read_text(encoding="utf-8"))
        document["speech"]["language"] = "unknown"
        broken = json.dumps(document).encode()
    else:
        path = "references/schemas/formula-image-ground-truth.schema.json"
        broken = b'{"type": 123}'
        reason = "invalid_reference_schema"

    provider = real_contract["provider"]
    provider._http.close()
    provider._http, _ = _mock_github({path: broken})
    for operation in (
        lambda: provider.get_item(DATASET_ID, item_id, "input"),
        lambda: provider.list_items(DATASET_ID, "input"),
        lambda: provider.list_splits(DATASET_ID),
    ):
        with pytest.raises(DatasetProviderError, match="invalid reference") as error:
            operation()
        assert error.value.details["reason"] == reason
    if reference_format == "csv":
        assert provider.get_item(DATASET_ID, "010", "input").annotations


@pytest.mark.parametrize(
    "split,item_id,path",
    [
        ("input", "009", "input/formula-images/simples_limpa.png"),
        ("intermediate", "001", "intermediate/processing-manifest/001.json"),
    ],
)
def test_missing_manifest_artifacts_are_rejected_before_advertising(
    real_contract, split, item_id, path,
):
    provider = real_contract["provider"]
    provider._http.close()
    provider._http, _ = _mock_github({path: None})
    with pytest.raises(DatasetProviderError, match=f"required artifact file.*{path}"):
        provider.get_item(DATASET_ID, item_id, split)
    with pytest.raises(DatasetProviderError, match=f"required artifact file.*{path}"):
        provider.list_items(DATASET_ID, split)


@pytest.mark.parametrize(
    ("schema", "error"),
    [
        (REFERENCE_MANIFEST_SCHEMA, None),
        (
            b'{"x-manifest-schema-version":"9.9.9"}',
            "unsupported references manifest schema version",
        ),
        (b"{", "invalid references manifest schema"),
        (b"{}", "x-manifest-schema-version must be a non-empty string"),
        (
            b'{"x-manifest-schema-version":"1.2.0"}',
            "expected the reference row type and CSV columns",
        ),
        (None, "required dataset manifest is missing: references/manifest.schema.json"),
    ],
)
def test_header_only_reference_manifest_checks_schema_version(schema, error):
    files = {
        "input/manifest.csv": b"id,input_path,format,media_type\n",
        "references/manifest.csv": _csv_bytes(list(REFERENCE_MANIFEST_COLUMNS), []),
    }
    if schema is not None:
        files[REFERENCE_MANIFEST_SCHEMA_PATH] = schema
    client, requests = _mock_github(files, root=None)
    provider = GitHubDatasetProvider(
        ProviderDescriptor(id="dataset-github", capabilities=DATASET_CAPABILITIES)
    )
    provider._http.close()
    provider._http = client
    try:
        if error:
            with pytest.raises(DatasetProviderError, match=error):
                provider.list_splits(DATASET_ID)
            return
        assert [split.id for split in provider.list_splits(DATASET_ID)] == ["input"]
        assert provider.list_items(DATASET_ID, "input") == []
        schema_revisions = [
            revision for _, revision, path in requests if path == REFERENCE_MANIFEST_SCHEMA_PATH
        ]
        assert schema_revisions == [DATASET_REFERENCE_REVISION]
    finally:
        provider._http.close()


def test_provider_default_can_be_configured_to_an_immutable_revision(real_contract):
    legacy = "9ed07b28aeddb69721228f2dd66de6f9794c0001"
    descriptor = ProviderDescriptor(
        id="dataset-github", capabilities=DATASET_CAPABILITIES,
        config={"default_revision": legacy.upper()},
    )
    provider = GitHubDatasetProvider(descriptor)
    try:
        assert provider.resolve_revision(DATASET_ID) == legacy
        assert (
            provider.resolve_revision(DATASET_ID, DATASET_REFERENCE_REVISION)
            == DATASET_REFERENCE_REVISION
        )
        assert provider.cache_namespace != real_contract["provider"].cache_namespace
    finally:
        provider._http.close()

    bad_descriptor = descriptor.model_copy(update={"config": {"default_revision": "main"}})
    with pytest.raises(ValueError, match="default_revision must be a full commit SHA"):
        GitHubDatasetProvider(bad_descriptor)


def test_effective_sha_separates_warmed_metadata_and_artifact_cache(tmp_path: Path):
    from acessilia_toolbox.core.dataset import ItemSummary
    from acessilia_toolbox.providers.dataset import DatasetAdapter

    class MutableProvider:
        default_sha = "a" * 40
        descriptor = ProviderDescriptor(
            id="dataset-github",
            version="2",
            capabilities=["dataset.list_items", "dataset.get_artifact"],
        )

        @property
        def cache_namespace(self):
            return f"contract@2:{self.default_sha}"

        def resolve_revision(self, dataset_id, revision=None):
            return revision or self.default_sha

        def list_items(self, dataset_id, split, revision, limit=100, offset=0):
            return [
                ItemSummary(
                    id=revision,
                    dataset=dataset_id,
                    split=split,
                    revision=revision,
                )
            ]

        def get_artifact(self, dataset_id, artifact_path, revision):
            return revision.encode(), "text/plain"

    provider = MutableProvider()
    cache = DictCache()
    descriptor = provider.descriptor.model_copy(
        update={"config": {"mirror": True}}
    )
    adapter = DatasetAdapter(
        descriptor,
        provider,
        cache=cache,
        store=FilesystemArtifactStore(tmp_path),
    )
    params = {
        "dataset_id": DATASET_ID,
        "split": "input",
        "limit": 1,
        "offset": 0,
    }
    old = adapter.execute("dataset.list_items", b"{}", parameters=params).document
    assert old[0]["id"] == "a" * 40
    assert adapter.execute("dataset.list_items", b"{}", parameters=params).document == old

    old_artifact = adapter.execute(
        "dataset.get_artifact",
        b"{}",
        parameters={"dataset_id": DATASET_ID, "artifact_path": "input/001.pdf"},
    ).document
    assert bytes.fromhex(old_artifact["payload"]) == b"a" * 40

    provider.default_sha = "b" * 40
    new = adapter.execute("dataset.list_items", b"{}", parameters=params).document
    assert new[0]["id"] == "b" * 40
    assert new[0]["revision"] == "b" * 40
    new_artifact = adapter.execute(
        "dataset.get_artifact",
        b"{}",
        parameters={"dataset_id": DATASET_ID, "artifact_path": "input/001.pdf"},
    ).document
    assert bytes.fromhex(new_artifact["payload"]) == b"b" * 40
    assert new_artifact["revision"] == "b" * 40
