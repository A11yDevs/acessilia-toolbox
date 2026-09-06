"""Execution provenance.

Named `ExecutionProvenance` to avoid colliding with the document-level
`Provenance` of the canonical structured document, which records where an
element sits on a page.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExecutionProvenance(BaseModel):
    """Everything needed to reproduce, audit or invalidate a result."""

    model_config = ConfigDict(extra="forbid")

    capability: str
    capability_version: int = Field(ge=1)
    provider: str
    provider_version: str
    input_fingerprints: list[str] = Field(default_factory=list)
    output_fingerprints: list[str] = Field(default_factory=list)
    parameters_hash: str
    model_versions: dict[str, str] = Field(default_factory=dict)
    started_at: datetime
    completed_at: datetime
    duration_ms: int = Field(ge=0)
    cache_key: str | None = None
    cache_hit: bool = False

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
