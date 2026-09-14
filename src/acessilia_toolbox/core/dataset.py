"""Normalized dataset model for the Acessilia Toolbox.

Datasets are read-only, revision-pinned collections of items organised into
splits.  The model is dataset-agnostic: it describes what a dataset *is*,
not how it is stored.  Provider adapters map remote layouts (GitHub repos,
HuggingFace datasets, …) onto this model.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SourceType = Literal["github", "huggingface"]


class DatasetInfo(BaseModel):
    """Top-level descriptor for one dataset."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, description="Stable dataset identifier")
    name: str = Field(min_length=1, description="Human-readable name")
    description: str = ""
    source: str = Field(min_length=1, description="Origin URL")
    source_type: SourceType = "github"
    default_revision: str = "main"
    revisions: list[str] = Field(default_factory=list)
    splits: list[str] = Field(default_factory=list)
    item_count: int | None = None
    license: str | None = None
    citation: str | None = None


class SplitInfo(BaseModel):
    """One split within a dataset revision."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = ""
    description: str = ""
    item_count: int = Field(default=0, ge=0)
    has_ground_truth: bool = False
    artifact_types: list[str] = Field(default_factory=list)


class ItemSummary(BaseModel):
    """Lightweight item reference returned by list operations.

    Carries no artifact payloads - use `get_item` for the full item.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    dataset: str = ""
    split: str = ""
    revision: str = ""
    artifact_count: int = Field(default=0, ge=0)
    has_annotations: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class DatasetArtifactRef(BaseModel):
    """Reference to one artifact inside a dataset item."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description="Relative path within the dataset")
    media_type: str = "application/octet-stream"
    size: int = Field(default=0, ge=0)
    sha256: str | None = None
    mirrored: bool = False
    artifact_id: str | None = None


class DatasetItem(BaseModel):
    """Full item with artifact references and annotations."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    dataset: str = ""
    split: str = ""
    revision: str = ""
    artifacts: list[DatasetArtifactRef] = Field(default_factory=list)
    annotations: list[DatasetArtifactRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
