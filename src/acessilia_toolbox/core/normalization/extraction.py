"""Provider extraction result consumed by the normalization builder."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ExtractionResult:
    """Raw output of a provider, before normalization.

    `document` holds the provider's own document object. Everything past this
    boundary works on the canonical structure instead.
    """

    document: Any
    backend: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    version: str
    configuration: dict[str, Any] = field(default_factory=dict)
