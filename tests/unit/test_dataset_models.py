"""Unit tests for dataset Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from acessilia_toolbox.core.dataset import (
    DatasetArtifactRef,
    DatasetInfo,
    DatasetItem,
    ItemSummary,
    SplitInfo,
)


def test_dataset_info_minimal() -> None:
    info = DatasetInfo(id="test-ds", name="Test Dataset", source="https://example.com", source_type="github")
    assert info.id == "test-ds"
    assert info.default_revision == "main"
    assert info.splits == []


def test_dataset_info_full() -> None:
    info = DatasetInfo(
        id="dr-docbench",
        name="Dr.DocBench",
        description="A benchmark dataset",
        source="https://huggingface.co/datasets/2077AIDataFoundation/DrDocBench",
        source_type="huggingface",
        default_revision="main",
        revisions=["abc123", "def456"],
        splits=["dev", "test"],
        item_count=1495,
        license="CC0",
        citation="arXiv 2606.01393",
    )
    assert info.item_count == 1495
    assert info.source_type == "huggingface"


def test_dataset_info_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DatasetInfo(id="x", name="x", source="x", source_type="github", unknown=True)  # type: ignore[call-arg]


def test_split_info() -> None:
    split = SplitInfo(
        id="dev",
        name="Development",
        description="With ground truth",
        item_count=986,
        has_ground_truth=True,
        artifact_types=["image/jpeg", "application/json"],
    )
    assert split.has_ground_truth
    assert split.item_count == 986


def test_split_info_defaults() -> None:
    split = SplitInfo(id="test", item_count=0, has_ground_truth=False)
    assert not split.has_ground_truth
    assert split.artifact_types == []


def test_item_summary() -> None:
    summary = ItemSummary(
        id="001",
        dataset="acessilia-dataset",
        split="input",
        revision="main",
        artifact_count=3,
        has_annotations=True,
        metadata={"pages": 42, "language": "pt-BR"},
    )
    assert summary.id == "001"
    assert summary.metadata["pages"] == 42


def test_dataset_artifact_ref() -> None:
    ref = DatasetArtifactRef(
        path="input/001.pdf",
        media_type="application/pdf",
        size=102400,
        sha256="abc123",
    )
    assert not ref.mirrored
    assert ref.artifact_id is None


def test_dataset_artifact_ref_mirrored() -> None:
    ref = DatasetArtifactRef(
        path="input/001.pdf",
        media_type="application/pdf",
        mirrored=True,
        artifact_id="sha256:abcdef",
    )
    assert ref.mirrored
    assert ref.artifact_id == "sha256:abcdef"


def test_dataset_item() -> None:
    item = DatasetItem(
        id="001",
        dataset="acessilia-dataset",
        split="input",
        revision="main",
        artifacts=[
            DatasetArtifactRef(path="input/001.pdf", media_type="application/pdf"),
        ],
        annotations=[
            DatasetArtifactRef(path="input/manifest.csv", media_type="text/csv"),
        ],
        metadata={"pages": 42},
    )
    assert len(item.artifacts) == 1
    assert len(item.annotations) == 1
    assert item.metadata["pages"] == 42


def test_dataset_item_minimal() -> None:
    item = DatasetItem(id="p001")
    assert item.artifacts == []
    assert item.annotations == []