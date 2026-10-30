"""Wire schemas for the REST facade.

Versioned independently from provider APIs, and derived from capability
manifests so REST, MCP and PDDL keep describing the same contract.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from acessilia_toolbox.core.artifact import ArtifactRef
from acessilia_toolbox.core.capability import CapabilityManifest
from acessilia_toolbox.core.provenance import ExecutionProvenance


class HealthResponse(BaseModel):
    status: str
    version: str
    git_commit: str = ""
    image_tag: str = ""


class CapabilitySummary(BaseModel):
    id: str
    version: int
    description: str
    input_schema: str
    output_schema: str
    media_types: list[str]
    deterministic: bool
    cacheable: bool
    #: Providers registered in this deployment that implement the capability:
    #: what a client can execute. The executor resolves any registered provider
    #: that implements the capability, so this may include providers the
    #: manifest does not bind; it is not a subset of ``declared_providers``.
    providers: list[str]
    #: Providers the manifest binds, including opt-in ones not registered here.
    declared_providers: list[str]
    #: Whether this is the highest version of the capability: the one a request
    #: without a version executes.
    latest: bool = True

    @classmethod
    def of(
        cls,
        manifest: CapabilityManifest,
        registered: list[str] | None = None,
        *,
        latest: bool = True,
    ) -> CapabilitySummary:
        declared = [binding.id for binding in manifest.providers]
        return cls(
            id=manifest.id,
            version=manifest.version,
            latest=latest,
            description=manifest.description,
            input_schema=manifest.input.schema_ref,
            output_schema=manifest.output.schema_ref,
            media_types=manifest.input.media_types,
            deterministic=manifest.execution.deterministic,
            cacheable=manifest.execution.cacheable,
            providers=sorted(registered) if registered is not None else declared,
            declared_providers=declared,
        )


class CapabilityDetail(CapabilitySummary):
    requires: list[str]
    produces: list[str]
    timeout_hint_seconds: int

    @classmethod
    def of(
        cls,
        manifest: CapabilityManifest,
        registered: list[str] | None = None,
        *,
        latest: bool = True,
    ) -> CapabilityDetail:
        summary = CapabilitySummary.of(manifest, registered, latest=latest)
        return cls(
            **summary.model_dump(),
            requires=manifest.semantics.requires,
            produces=manifest.semantics.produces,
            timeout_hint_seconds=manifest.execution.timeout_hint_seconds,
        )


class ExecutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    capability: str
    provider: str
    artifacts: list[ArtifactRef]
    document: Any
    provenance: ExecutionProvenance


class ErrorResponse(BaseModel):
    """Machine-readable error; never carries provider credentials."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class OpenAiError(BaseModel):
    message: str
    type: str
    code: str | None = None


class OpenAiErrorResponse(BaseModel):
    """Error of the VLM chat gateway, in the shape OpenAI-compatible clients read."""

    error: OpenAiError
