"""Canonical structured document — Pydantic v2 models for document structure.

The wire contract remains the Processing Manifest 1.1.0 so existing Acessilia
consumers keep working; the toolbox exposes it as artifact/structured-document@1.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Final = "1.1.0"
SCHEMA_ID: Final = "urn:a11y-devs:schema:processing-manifest:1.1.0"

ElementType = Literal[
    "title",
    "heading",
    "paragraph",
    "list_item",
    "table",
    "picture",
    "formula",
    "code",
    "caption",
    "footnote",
    "page_header",
    "page_footer",
    "checkbox",
    "key_value",
    "form",
    "group",
    "unknown",
]
Severity = Literal["info", "warning", "error"]
ObligationStatus = Literal["candidate", "pending", "in_progress", "satisfied", "failed"]
AttemptStatus = Literal["succeeded", "failed", "rejected"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoundingBox(StrictModel):
    left: float
    top: float
    right: float
    bottom: float
    coord_origin: Literal["TOPLEFT", "BOTTOMLEFT", "UNKNOWN"] = "UNKNOWN"


class Provenance(StrictModel):
    page_number: int = Field(ge=1)
    bbox: BoundingBox | None = None
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)


class SourceDocument(StrictModel):
    document_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExtractorRun(StrictModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    started_at: datetime
    completed_at: datetime
    duration_ms: int = Field(ge=0)
    configuration: dict[str, Any] = Field(default_factory=dict)


class PageDescriptor(StrictModel):
    page_number: int = Field(ge=1)
    width: float | None = Field(default=None, gt=0)
    height: float | None = Field(default=None, gt=0)
    element_ids: list[str] = Field(default_factory=list)


class ManifestElement(StrictModel):
    id: str = Field(min_length=1)
    type: ElementType
    raw_label: str = Field(min_length=1)
    reading_order: int = Field(ge=1)
    hierarchy_level: int = Field(ge=0)
    text: str | None = None
    source_ref: str | None = None
    parent_ref: str | None = None
    parent_id: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    provenance: list[Provenance] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Observation(StrictModel):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    severity: Severity
    message: str = Field(min_length=1)
    target_ids: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ObligationAttempt(StrictModel):
    method: str = Field(min_length=1)
    status: AttemptStatus
    started_at: datetime
    completed_at: datetime
    message: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_interval(self) -> ObligationAttempt:
        if self.completed_at < self.started_at:
            raise ValueError("attempt finished before it started")
        return self


class Obligation(StrictModel):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    status: ObligationStatus = "candidate"
    selected: bool = False
    target_ids: list[str] = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    admissible_methods: list[str] = Field(default_factory=list)
    method_costs: dict[str, int] = Field(default_factory=dict)
    attempts: list[ObligationAttempt] = Field(default_factory=list)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_methods(self) -> Obligation:
        if self.admissible_methods and self.method_costs:
            unknown = set(self.method_costs) - set(self.admissible_methods)
            if unknown:
                raise ValueError(
                    f"costs given for non-admissible methods: {sorted(unknown)}"
                )
        return self


class Artifact(StrictModel):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ManifestSummary(StrictModel):
    page_count: int = Field(ge=0)
    element_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    obligation_count: int = Field(ge=0)
    element_types: dict[str, int] = Field(default_factory=dict)


class ProcessingManifest(StrictModel):
    schema_ref: str = Field(default=SCHEMA_ID, alias="$schema")
    schema_version: Literal["1.1.0"] = SCHEMA_VERSION
    manifest_id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    status: Literal["extracted", "planned", "processing", "completed", "failed"] = (
        "extracted"
    )
    created_at: datetime
    source: SourceDocument
    extractor: ExtractorRun
    title: str = Field(min_length=1)
    language: str = Field(min_length=2)
    pages: list[PageDescriptor]
    elements: list[ManifestElement]
    observations: list[Observation] = Field(default_factory=list)
    obligations: list[Obligation] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    summary: ManifestSummary

    @model_validator(mode="after")
    def validate_references(self) -> ProcessingManifest:
        known_elements = {element.id for element in self.elements}
        known_obligations = {obligation.id for obligation in self.obligations}
        artifact_ids = {artifact.id for artifact in self.artifacts}

        # Validate no duplicate page numbers
        if len(self.pages) != len({page.page_number for page in self.pages}):
            raise ValueError("duplicate page numbers")

        # Validate page references to known elements
        for page in self.pages:
            unknown = set(page.element_ids) - known_elements
            if unknown:
                raise ValueError(
                    f"page {page.page_number} references unknown elements: "
                    f"{sorted(unknown)}"
                )

        # Validate observations
        for observation in self.observations:
            unknown = set(observation.target_ids) - known_elements
            if unknown:
                raise ValueError(
                    f"observation {observation.id} references unknown targets: "
                    f"{sorted(unknown)}"
                )

        # Validate obligations
        for obligation in self.obligations:
            unknown_targets = set(obligation.target_ids) - known_elements
            if unknown_targets:
                raise ValueError(
                    f"obligation {obligation.id} references unknown targets: "
                    f"{sorted(unknown_targets)}"
                )
            unknown_dependencies = set(obligation.dependencies) - known_obligations
            if unknown_dependencies:
                raise ValueError(
                    f"obligation {obligation.id} references unknown dependencies: "
                    f"{sorted(unknown_dependencies)}"
                )
            if obligation.id in obligation.dependencies:
                raise ValueError(f"obligation {obligation.id} depends on itself")
            for attempt in obligation.attempts:
                if attempt.method not in obligation.admissible_methods:
                    raise ValueError(
                        f"non-admissible method attempted on {obligation.id}: "
                        f"{attempt.method}"
                    )
                unknown_artifacts = set(attempt.artifact_ids) - set(artifact_ids)
                if unknown_artifacts:
                    raise ValueError(
                        f"attempt on {obligation.id} references unknown "
                        f"artifacts: {sorted(unknown_artifacts)}"
                    )

        # Dependency cycle
        dependency_graph = {
            obligation.id: obligation.dependencies for obligation in self.obligations
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(obligation_id: str) -> None:
            if obligation_id in visiting:
                raise ValueError("cycle detected in obligation dependencies")
            if obligation_id in visited:
                return
            visiting.add(obligation_id)
            for dependency in dependency_graph.get(obligation_id, []):
                visit(dependency)
            visiting.remove(obligation_id)
            visited.add(obligation_id)

        for obligation_id in dependency_graph:
            visit(obligation_id)

        obligation_by_id = {
            obligation.id: obligation for obligation in self.obligations
        }
        for obligation in self.obligations:
            if obligation.status != "satisfied":
                continue
            unsatisfied = [
                dependency
                for dependency in obligation.dependencies
                if obligation_by_id[dependency].status != "satisfied"
            ]
            if unsatisfied:
                raise ValueError(
                    f"satisfied obligation {obligation.id} has unsatisfied "
                    f"predecessors: {unsatisfied}"
                )

        # Validar summary
        expected_counts = {
            "page_count": len(self.pages),
            "element_count": len(self.elements),
            "observation_count": len(self.observations),
            "obligation_count": len(self.obligations),
        }
        for field, expected in expected_counts.items():
            if getattr(self.summary, field) != expected:
                raise ValueError(
                    f"summary.{field}={getattr(self.summary, field)}; expected {expected}"
                )
        return self

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": SCHEMA_ID,
            "title": "ProcessingManifest",
            # Verbatim: this string is part of the published 1.1.0 schema.
            "description": (
                "Operational manifest generated by the "
                "Acessilia structural extractor."
            ),
        },
    )
