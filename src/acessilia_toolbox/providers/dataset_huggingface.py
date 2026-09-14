"""Dataset provider for HuggingFace-hosted datasets (e.g. Dr.DocBench).

Fetches dataset metadata and file content via the HuggingFace Hub API.
Uses only ``httpx`` (already a core dependency); ``huggingface_hub`` is an
optional extra for advanced features.
"""

from __future__ import annotations

import json
import os
import re
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
# Known dataset definitions
# ---------------------------------------------------------------------------

DATASET_DEFS: dict[str, dict[str, Any]] = {
    "dr-docbench": {
        "name": "Dr.DocBench",
        "description": (
            "Difficulty-aware document understanding benchmark. "
            "Contains academic and specialist documents across 52 BISAC "
            "subjects with page-level annotations in OmniDocJSON format. "
            "The `dev` split includes ground truth; the `test` split "
            "contains only images (ground truth withheld for EvalAI)."
        ),
        "source": "https://huggingface.co/datasets/2077AIDataFoundation/DrDocBench",
        "source_type": "huggingface",
        "default_revision": "main",
        "hf_repo": "2077AIDataFoundation/DrDocBench",
        "splits": {
            "dev": {
                "name": "Development",
                "description": "986 pages with full OmniDocJSON ground truth",
                "has_ground_truth": True,
                "subdir": "dev",
            },
            "test": {
                "name": "Test",
                "description": "509 pages, images only – ground truth withheld for EvalAI",
                "has_ground_truth": False,
                "subdir": "test",
            },
        },
    },
}


class HuggingFaceDatasetProvider:
    """Fetches dataset metadata and artifacts from HuggingFace Hub."""

    HF_API = "https://huggingface.co/api/datasets"
    HF_RESOLVE = "https://huggingface.co/datasets"

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self._http = httpx.Client(timeout=30.0, follow_redirects=True)
        self._hf_token = os.getenv("HF_TOKEN", "")
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
        self, dataset_id: str, revision: str | None = None  # noqa: ARG002
    ) -> Sequence[SplitInfo]:
        ddef = self._resolve_def(dataset_id)
        splits: list[SplitInfo] = []
        for sid, sdef in ddef["splits"].items():
            items = self._list_items_raw(dataset_id, sid, revision)
            splits.append(
                SplitInfo(
                    id=sid,
                    name=sdef["name"],
                    description=sdef["description"],
                    item_count=len(items),
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
        self._resolve_split(ddef, split)
        items = self._list_items_raw(dataset_id, split, revision)
        page = items[offset: offset + limit]
        return [
            ItemSummary(
                id=item["id"],
                dataset=dataset_id,
                split=split,
                revision=revision or ddef["default_revision"],
                artifact_count=item.get("artifact_count", 1),
                has_annotations=ddef["splits"][split]["has_ground_truth"],
                metadata=item.get("metadata", {}),
            )
            for item in page
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
        items = self._list_items_raw(dataset_id, split, revision)

        match = next((i for i in items if i["id"] == item_id), None)
        if match is None:
            raise ItemNotFoundError(
                f"item {item_id!r} not found in {dataset_id}/{split}",
                dataset=dataset_id,
                split=split,
                item=item_id,
            )

        ref = revision or ddef["default_revision"]
        subdir = sdef["subdir"]

        artifacts: list[DatasetArtifactRef] = []
        annotations: list[DatasetArtifactRef] = []

        # Image artifact
        img_path = f"{subdir}/{match['subject']}/{match['doc_uuid']}/images/{match['page_file']}"
        artifacts.append(
            DatasetArtifactRef(path=img_path, media_type="image/jpeg")
        )

        # Annotations (dev split only)
        if sdef["has_ground_truth"]:
            json_path = (
                f"{subdir}/{match['subject']}/{match['doc_uuid']}/"
                f"json/{match['doc_uuid']}_page_{match['page_no']}.json"
            )
            annotations.append(
                DatasetArtifactRef(path=json_path, media_type="application/json")
            )

            md_path = (
                f"{subdir}/{match['subject']}/{match['doc_uuid']}/"
                f"mds/{match['doc_uuid']}_page_{match['page_no']}.md"
            )
            annotations.append(
                DatasetArtifactRef(path=md_path, media_type="text/markdown")
            )

        return DatasetItem(
            id=item_id,
            dataset=dataset_id,
            split=split,
            revision=ref,
            artifacts=artifacts,
            annotations=annotations,
            metadata=match.get("metadata", {}),
        )

    def get_artifact(
        self,
        dataset_id: str,
        artifact_path: str,
        revision: str | None = None,
    ) -> tuple[bytes, str]:
        ddef = self._resolve_def(dataset_id)
        ref = revision or ddef["default_revision"]
        hf_repo = ddef["hf_repo"]

        url = f"{self.HF_RESOLVE}/{hf_repo}/resolve/{ref}/{artifact_path}"
        headers: dict[str, str] = {}
        if self._hf_token:
            headers["Authorization"] = f"Bearer {self._hf_token}"

        resp = self._http.get(url, headers=headers)
        if resp.status_code == 404:
            raise ItemNotFoundError(
                f"artifact not found: {artifact_path}",
                dataset=dataset_id,
                path=artifact_path,
            )
        if resp.status_code != 200:
            raise DatasetProviderError(
                f"HF API returned {resp.status_code} for {artifact_path}",
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
        self._resolve_split(ddef, split)
        items = self._list_items_raw(dataset_id, split, revision)
        selected = random.sample(items, min(n, len(items)))
        return [
            self.get_item(dataset_id, item["id"], split, revision)
            for item in selected
        ]

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
            id=dataset_id,
            name=ddef["name"],
            description=ddef["description"],
            source=ddef["source"],
            source_type=ddef["source_type"],
            default_revision=ddef["default_revision"],
            splits=list(ddef["splits"]),
        )

    def _list_items_raw(
        self,
        dataset_id: str,
        split: str,
        revision: str | None = None,
    ) -> list[dict[str, Any]]:
        """List items by walking the HF dataset directory tree via the API.

        Returns a list of dicts with keys: id, subject, doc_uuid, page_no,
        page_file, artifact_count, metadata.
        """
        ddef = self._resolve_def(dataset_id)
        sdef = self._resolve_split(ddef, split)
        ref = revision or ddef["default_revision"]
        subdir = sdef["subdir"]
        hf_repo = ddef["hf_repo"]

        # List BISAC subject directories
        subjects = self._list_dir(hf_repo, ref, subdir)
        items: list[dict[str, Any]] = []

        for subject in subjects:
            doc_dirs = self._list_dir(hf_repo, ref, f"{subdir}/{subject}")
            for doc_uuid in doc_dirs:
                images = self._list_dir(
                    hf_repo, ref, f"{subdir}/{subject}/{doc_uuid}/images"
                )
                for img_name in images:
                    # Parse page number from filename: page_<N>.jpg
                    m = re.search(r"page_(\d+)", img_name)
                    page_no = int(m.group(1)) if m else 0
                    item_id = f"{doc_uuid}_p{page_no}"
                    items.append(
                        {
                            "id": item_id,
                            "subject": subject,
                            "doc_uuid": doc_uuid,
                            "page_no": page_no,
                            "page_file": img_name,
                            "artifact_count": 1,
                            "metadata": {
                                "subject": subject,
                                "document": doc_uuid,
                                "page": page_no,
                            },
                        }
                    )

        return items

    def _list_dir(
        self, hf_repo: str, revision: str, path: str
    ) -> list[str]:
        """List directory entries via the HF Hub API."""
        url = f"{self.HF_API}/{hf_repo}/refs/{revision}/tree/{path}"
        headers: dict[str, str] = {}
        if self._hf_token:
            headers["Authorization"] = f"Bearer {self._hf_token}"

        resp = self._http.get(url, headers=headers)
        if resp.status_code != 200:
            return []

        try:
            entries = resp.json()
        except (json.JSONDecodeError, TypeError):
            return []

        return [
            e["name"] + "/" if e.get("type") == "directory" else e["name"]
            for e in entries
            if isinstance(e, dict)
        ]

    def _artifact_types_for_split(self, split: str) -> list[str]:
        if split == "dev":
            return ["image/jpeg", "application/json", "text/markdown"]
        return ["image/jpeg"]
