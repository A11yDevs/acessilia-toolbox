"""Published bindings of dedicated dataset REST routes (#158).

The capability's execute output can name a shared family. A dedicated REST
route names its precise response schema instead (summaries versus full items).
This metadata serves OpenAPI and the static contract without importing FastAPI
or changing response validation/serialization.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class DatasetRouteBinding:
    method: Literal["GET", "POST"]
    path: str
    capability: str
    output_schema: str
    capability_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def openapi(self) -> dict[str, Any]:
        """An operation extension; schema_refs resolves its output to a file."""
        return {"x-toolbox-dataset-binding": self.to_dict()}


_BASE = "/v1/datasets"
_ITEMS = _BASE + "/{dataset_id}/splits/{split}/items"

DATASET_REST_BINDINGS = {
    binding.capability: binding
    for binding in (
        DatasetRouteBinding("GET", _BASE, "dataset.list", "artifact/dataset-list@1"),
        DatasetRouteBinding(
            "GET", _BASE + "/{dataset_id}", "dataset.describe", "artifact/dataset-info@1",
        ),
        DatasetRouteBinding(
            "GET", _BASE + "/{dataset_id}/splits", "dataset.list_splits",
            "artifact/dataset-splits@1",
        ),
        DatasetRouteBinding(
            "GET", _ITEMS, "dataset.list_items", "artifact/dataset-item-summary-list@1",
        ),
        DatasetRouteBinding(
            "GET", _ITEMS + "/{item_id}", "dataset.get_item", "artifact/dataset-item@1",
        ),
        DatasetRouteBinding(
            "GET", _ITEMS + "/{item_id}/artifacts/{path}", "dataset.get_artifact",
            "artifact/binary@1",
        ),
        DatasetRouteBinding(
            "GET", _BASE + "/{dataset_id}/splits/{split}/sample", "dataset.sample",
            "artifact/dataset-sample@1",
        ),
        DatasetRouteBinding(
            "POST", _BASE + "/{dataset_id}/sync", "dataset.sync",
            "artifact/dataset-sync-report@1",
        ),
    )
}
