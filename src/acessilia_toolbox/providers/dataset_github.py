"""Dataset provider for GitHub-hosted datasets (e.g. Acessilia Dataset).

Fetches repository metadata and raw file content via the GitHub API and
raw content CDN.  Uses only ``httpx`` (already a core dependency).
"""

from __future__ import annotations

import csv
import io
import os
from collections.abc import Sequence
from typing import Any

import httpx

from acessilia_toolbox.core.dataset import (
    DatasetArtifactRef,
    DatasetInfo,
    DatasetItem,
    ItemSummary,
    SplitInfo,
)
from acessilia_toolbox.core.errors import (
    DatasetNotFoundError,
    DatasetProviderError,
    ItemNotFoundError,
    SplitNotFoundError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor

# ---------------------------------------------------------------------------
# Known dataset definitions - extend this dict to add a third dataset.
# ---------------------------------------------------------------------------

DATASET_DEFS: dict[str, dict[str, Any]] = {
    "acessilia-dataset": {
        "name": "Acessilia Dataset",
        "description": (
            "Reference dataset for accessibility processing pipelines. "
            "Contains PDFs, images, formulas and ground-truth annotations "
            "for testing document structure extraction, math recognition, "
            "and accessible output generation."
        ),
        "source": "https://github.com/A11yDevs/acessilia-dataset",
        "source_type": "github",
        "default_revision": "main",
        "owner": "A11yDevs",
        "repo": "acessilia-dataset",
        "splits": {
            "input": {
                "name": "Input Documents",
                "description": "Source documents (PDFs, images) for testing",
                "has_ground_truth": True,
                "manifest_path": "input/manifest.csv",
                "formula_gt_path": "input/formula-images/ground_truth.csv",
            },
            "intermediate": {
                "name": "Intermediate Artifacts",
                "description": "Processing manifests, canonical documents, PDDL plans",
                "has_ground_truth": False,
                "manifest_path": "intermediate/manifest.csv",
            },
            "outputs": {
                "name": "Accessible Outputs",
                "description": "Expected accessible outputs (TXT, HTML, PDF/UA, EPUB, MP3)",
                "has_ground_truth": True,
                "manifest_path": "outputs/manifest.csv",
            },
        },
    },
}


class GitHubDatasetProvider:
    """Fetches dataset metadata and artifacts from a GitHub repository."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self._http = httpx.Client(timeout=30.0, follow_redirects=True)
        self._github_token = os.getenv("GITHUB_TOKEN", "")
        self._defs = dict(DATASET_DEFS)

    # ------------------------------------------------------------------
    # DatasetProvider protocol
    # ------------------------------------------------------------------

    def list_datasets(self) -> Sequence[DatasetInfo]:
        return [self._build_info(did, ddef) for did, ddef in self._defs.items()]

    def describe(self, dataset_id: str, revision: str | None = None) -> DatasetInfo:
        ddef = self._resolve_def(dataset_id)
        info = self._build_info(dataset_id, ddef)
        if revision is not None:
            info.default_revision = revision
        return info

    def list_splits(
        self, dataset_id: str, revision: str | None = None
    ) -> Sequence[SplitInfo]:
        ddef = self._resolve_def(dataset_id)
        splits: list[SplitInfo] = []
        for sid, sdef in ddef["splits"].items():
            manifest = self._fetch_manifest(dataset_id, sdef["manifest_path"], revision)
            splits.append(
                SplitInfo(
                    id=sid,
                    name=sdef["name"],
                    description=sdef["description"],
                    item_count=len(manifest),
                    has_ground_truth=sdef["has_ground_truth"],
                    artifact_types=self._artifact_types_for_split(sid),
                )
            )
        return splits

    def list_items(
        self,
        dataset_id: str,
        split: str,
        revision: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[ItemSummary]:
        ddef = self._resolve_def(dataset_id)
        sdef = self._resolve_split(ddef, split)
        manifest = self._fetch_manifest(dataset_id, sdef["manifest_path"], revision)

        page = manifest[offset: offset + limit]
        return [
            ItemSummary(
                id=row["id"],
                dataset=dataset_id,
                split=split,
                revision=revision or ddef["default_revision"],
                artifact_count=self._estimate_artifact_count(row),
                has_annotations=sdef["has_ground_truth"],
                metadata=row,
            )
            for row in page
        ]

    def get_item(
        self,
        dataset_id: str,
        item_id: str,
        split: str,
        revision: str | None = None,
    ) -> DatasetItem:
        ddef = self._resolve_def(dataset_id)
        sdef = self._resolve_split(ddef, split)
        manifest = self._fetch_manifest(dataset_id, sdef["manifest_path"], revision)

        row = next((r for r in manifest if r["id"] == item_id), None)
        if row is None:
            raise ItemNotFoundError(
                f"item {item_id!r} not found in {dataset_id}/{split}",
                dataset=dataset_id,
                split=split,
                item=item_id,
            )

        artifacts, annotations = self._build_artifacts(
            split, row, sdef, revision
        )

        return DatasetItem(
            id=item_id,
            dataset=dataset_id,
            split=split,
            revision=revision or ddef["default_revision"],
            artifacts=artifacts,
            annotations=annotations,
            metadata=row,
        )

    def get_artifact(
        self,
        dataset_id: str,
        artifact_path: str,
        revision: str | None = None,
    ) -> tuple[bytes, str]:
        ddef = self._resolve_def(dataset_id)
        owner = ddef["owner"]
        repo = ddef["repo"]
        ref = revision or ddef["default_revision"]

        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{artifact_path}"
        headers: dict[str, str] = {}
        if self._github_token:
            headers["Authorization"] = f"Bearer {self._github_token}"

        resp = self._http.get(url, headers=headers)
        if resp.status_code == 404:
            raise ItemNotFoundError(
                f"artifact not found: {artifact_path}",
                dataset=dataset_id,
                path=artifact_path,
            )
        if resp.status_code != 200:
            raise DatasetProviderError(
                f"GitHub API returned {resp.status_code} for {artifact_path}",
                dataset=dataset_id,
                path=artifact_path,
                status=resp.status_code,
            )

        media_type = resp.headers.get("content-type", "application/octet-stream")
        return resp.content, media_type

    def sample(
        self,
        dataset_id: str,
        split: str,
        n: int = 5,
        revision: str | None = None,
    ) -> Sequence[DatasetItem]:
        import random

        ddef = self._resolve_def(dataset_id)
        sdef = self._resolve_split(ddef, split)
        manifest = self._fetch_manifest(dataset_id, sdef["manifest_path"], revision)

        selected = random.sample(manifest, min(n, len(manifest)))
        items: list[DatasetItem] = []
        for row in selected:
            artifacts, annotations = self._build_artifacts(
                split, row, sdef, revision
            )
            items.append(
                DatasetItem(
                    id=row["id"],
                    dataset=dataset_id,
                    split=split,
                    revision=revision or ddef["default_revision"],
                    artifacts=artifacts,
                    annotations=annotations,
                    metadata=row,
                )
            )
        return items

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_def(self, dataset_id: str) -> dict[str, Any]:
        ddef = self._defs.get(dataset_id)
        if ddef is None:
            raise DatasetNotFoundError(
                f"unknown dataset {dataset_id!r}",
                dataset=dataset_id,
                known=list(self._defs),
            )
        return ddef

    def _resolve_split(self, ddef: dict[str, Any], split: str) -> dict[str, Any]:
        sdef = ddef["splits"].get(split)
        if sdef is None:
            raise SplitNotFoundError(
                f"unknown split {split!r} in {ddef['name']}",
                dataset=ddef.get("id", ""),
                split=split,
                known=list(ddef["splits"]),
            )
        return sdef

    def _build_info(self, dataset_id: str, ddef: dict[str, Any]) -> DatasetInfo:
        return DatasetInfo(
            id=dataset_id, name=ddef["name"], description=ddef["description"],
            source=ddef["source"], source_type=ddef["source_type"],
            default_revision=ddef["default_revision"],
            splits=list(ddef["splits"]),
        )

    def _fetch_manifest(
        self, dataset_id: str, manifest_path: str, revision: str | None = None
    ) -> list[dict[str, str]]:
        """Fetch and parse a CSV manifest from the GitHub repo."""
        try:
            content, _ = self.get_artifact(dataset_id, manifest_path, revision)
        except (ItemNotFoundError, DatasetProviderError):
            return []

        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        return list(reader)

    def _artifact_types_for_split(self, split: str) -> list[str]:
        mapping = {
            "input": ["application/pdf", "image/jpeg", "image/png", "text/csv"],
            "intermediate": ["application/json"],
            "outputs": [
                "text/plain",
                "text/html",
                "application/pdf",
                "audio/mpeg",
                "application/epub+zip",
            ],
        }
        return mapping.get(split, ["application/octet-stream"])

    def _estimate_artifact_count(self, row: dict[str, str]) -> int:
        """Guess artifact count from manifest columns."""
        count = 1  # at least the source file
        for col in ("pages", "tables", "formulas", "images"):
            if col in row and row[col].strip().isdigit():
                count += int(row[col])
        return count

    def _build_artifacts(
        self,
        split: str,
        row: dict[str, str],
        sdef: dict[str, Any],
        revision: str | None = None,
    ) -> tuple[list[DatasetArtifactRef], list[DatasetArtifactRef]]:
        """Build artifact and annotation refs for one manifest row."""
        item_id = row["id"]
        subdir = row.get("subdirectory", "")

        artifacts: list[DatasetArtifactRef] = []
        annotations: list[DatasetArtifactRef] = []

        if split == "input":
            # Source file
            fmt = row.get("format", "pdf").lower()
            ext = {"pdf": "pdf", "jpeg": "jpeg", "jpg": "jpeg", "png": "png"}.get(fmt, fmt)
            media = {
                "pdf": "application/pdf",
                "jpeg": "image/jpeg",
                "png": "image/png",
            }.get(ext, "application/octet-stream")

            source_path = f"input/{subdir}/{item_id}.{ext}" if subdir else f"input/{item_id}.{ext}"
            artifacts.append(
                DatasetArtifactRef(path=source_path, media_type=media)
            )

            # Ground-truth CSVs
            annotations.append(
                DatasetArtifactRef(
                    path=sdef["manifest_path"],
                    media_type="text/csv",
                )
            )
            if sdef.get("formula_gt_path"):
                annotations.append(
                    DatasetArtifactRef(
                        path=sdef["formula_gt_path"],
                        media_type="text/csv",
                    )
                )

        elif split == "intermediate":
            for itype in ("processing-manifest", "canonical-document", "pddl-plan"):
                path = f"intermediate/{itype}/{item_id}.json"
                artifacts.append(
                    DatasetArtifactRef(path=path, media_type="application/json")
                )

        elif split == "outputs":
            for fmt_name in ("txt", "html", "pdf", "pdf_ua", "mp3", "epub"):
                ext_map = {
                    "txt": "txt",
                    "html": "html",
                    "pdf": "pdf",
                    "pdf_ua": "pdf",
                    "mp3": "mp3",
                    "epub": "epub",
                }
                media_map = {
                    "txt": "text/plain",
                    "html": "text/html",
                    "pdf": "application/pdf",
                    "pdf_ua": "application/pdf",
                    "mp3": "audio/mpeg",
                    "epub": "application/epub+zip",
                }
                path = f"outputs/{fmt_name}/{item_id}.{ext_map[fmt_name]}"
                artifacts.append(
                    DatasetArtifactRef(path=path, media_type=media_map[fmt_name])
                )

        return artifacts, annotations
