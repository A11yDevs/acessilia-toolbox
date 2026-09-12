"""REST facade over the capability registry."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response

from acessilia_toolbox import __version__
from acessilia_toolbox.api.auth import require_api_key
from acessilia_toolbox.api.schemas import (
    CapabilityDetail,
    CapabilitySummary,
    ErrorResponse,
    ExecutionResponse,
    HealthResponse,
)
from acessilia_toolbox.core.artifact import ArtifactRef
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import InvalidInputError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.pddl import (
    capability_action,
    domain_fragment,
    predicates_list,
)
from acessilia_toolbox.core.provider import ProviderHealth, ProviderRegistry
from acessilia_toolbox.providers import create_adapter

# Public router: endpoints that do not require authentication.
public_router = APIRouter(prefix="/v1")

# Authenticated router: every endpoint requires a valid API key.
router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    415: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}

MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def capabilities(request: Request) -> CapabilityRegistry:
    registry: CapabilityRegistry = request.app.state.capabilities
    return registry


def providers(request: Request) -> ProviderRegistry:
    registry: ProviderRegistry = request.app.state.providers
    return registry


def executor(request: Request) -> CapabilityExecutor:
    instance: CapabilityExecutor = request.app.state.executor
    return instance


@public_router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    return HealthResponse(status="healthy", version=__version__)


@router.get("/capabilities", response_model=list[CapabilitySummary], tags=["capabilities"])
def list_capabilities(
    registry: Annotated[CapabilityRegistry, Depends(capabilities)],
) -> list[CapabilitySummary]:
    return [CapabilitySummary.of(manifest) for manifest in registry.manifests()]


@router.get(
    "/capabilities/{capability_id}",
    response_model=CapabilityDetail,
    responses=ERROR_RESPONSES,
    tags=["capabilities"],
)
def get_capability(
    capability_id: str,
    registry: Annotated[CapabilityRegistry, Depends(capabilities)],
    version: int | None = None,
) -> CapabilityDetail:
    return CapabilityDetail.of(registry.get(capability_id, version))


@router.post(
    "/capabilities/{capability_id}:execute",
    response_model=ExecutionResponse,
    responses=ERROR_RESPONSES,
    tags=["capabilities"],
)
async def execute_capability(
    capability_id: str,
    runner: Annotated[CapabilityExecutor, Depends(executor)],
    file: Annotated[UploadFile | None, File(description="Document to process")] = None,
    artifact_id: Annotated[str | None, Form(description="Stored input")] = None,
    provider: Annotated[str | None, Form()] = None,
    parameters: Annotated[str | None, Form(description="JSON object")] = None,
    language: Annotated[str, Form()] = "pt-BR",
    version: Annotated[int | None, Form()] = None,
) -> ExecutionResponse:
    payload, filename, media_type = await _resolve_input(runner, file, artifact_id)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise InvalidInputError(
            f"upload exceeds {MAX_UPLOAD_BYTES} bytes", capability=capability_id
        )

    result = runner.execute(
        capability_id,
        payload,
        filename=filename,
        media_type=media_type,
        provider_id=provider,
        capability_version=version,
        parameters=_parse_parameters(parameters),
        language=language,
    )

    return ExecutionResponse(
        status=result.status,
        capability=result.capability,
        provider=result.provider,
        artifacts=result.artifacts,
        document=result.document,
        provenance=result.provenance,
    )


@router.post(
    "/artifacts",
    response_model=ArtifactRef,
    responses=ERROR_RESPONSES,
    tags=["artifacts"],
)
async def store_artifact(
    runner: Annotated[CapabilityExecutor, Depends(executor)],
    file: Annotated[UploadFile, File(description="Content to store")],
) -> ArtifactRef:
    payload = await file.read()
    if not payload:
        raise InvalidInputError("empty payload")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise InvalidInputError(f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
    return runner.store_artifact(
        payload,
        media_type=file.content_type or "application/octet-stream",
        filename=file.filename,
    )


@router.get("/artifacts/{artifact_id}", responses=ERROR_RESPONSES, tags=["artifacts"])
def retrieve_artifact(
    artifact_id: str,
    runner: Annotated[CapabilityExecutor, Depends(executor)],
) -> Response:
    payload, ref = runner.retrieve_artifact(artifact_id)
    headers = {"ETag": ref.artifact_id}
    if ref.filename:
        headers["Content-Disposition"] = f'attachment; filename="{ref.filename}"'
    return Response(content=payload, media_type=ref.media_type, headers=headers)


@router.get(
    "/artifacts/{artifact_id}/metadata",
    response_model=ArtifactRef,
    responses=ERROR_RESPONSES,
    tags=["artifacts"],
)
def artifact_metadata(
    artifact_id: str,
    runner: Annotated[CapabilityExecutor, Depends(executor)],
) -> ArtifactRef:
    return runner.retrieve_artifact(artifact_id)[1]


@router.get("/providers", tags=["providers"])
def list_providers(
    registry: Annotated[ProviderRegistry, Depends(providers)],
) -> list[dict[str, Any]]:
    return [descriptor.public_payload() for descriptor in registry.descriptors()]


@router.get("/providers/{provider_id}", responses=ERROR_RESPONSES, tags=["providers"])
def get_provider(
    provider_id: str,
    registry: Annotated[ProviderRegistry, Depends(providers)],
) -> dict[str, Any]:
    return registry.get(provider_id).public_payload()


@router.get(
    "/providers/{provider_id}/health",
    response_model=ProviderHealth,
    responses=ERROR_RESPONSES,
    tags=["providers"],
)
def provider_health(
    provider_id: str,
    registry: Annotated[ProviderRegistry, Depends(providers)],
) -> ProviderHealth:
    return create_adapter(registry.get(provider_id)).health()


def _parse_parameters(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidInputError(f"parameters must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InvalidInputError("parameters must be a JSON object")
    return parsed


async def _resolve_input(
    runner: CapabilityExecutor,
    file: UploadFile | None,
    artifact_id: str | None,
) -> tuple[bytes, str, str]:
    """Accept either a direct upload or a reference to stored content."""
    if file is not None and artifact_id:
        raise InvalidInputError("provide either a file or an artifact_id, not both")
    if file is not None:
        return (
            await file.read(),
            file.filename or "upload",
            file.content_type or "application/octet-stream",
        )
    if artifact_id:
        payload, ref = runner.retrieve_artifact(artifact_id)
        return payload, ref.filename or "artifact", ref.media_type
    raise InvalidInputError("a file or an artifact_id is required")


@router.get("/planning/domain", tags=["planning"])
def planning_domain(
    registry: Annotated[CapabilityRegistry, Depends(capabilities)],
) -> Response:
    return Response(
        content=domain_fragment(registry.manifests()),
        media_type="text/plain",
    )


@router.get("/planning/capabilities/{capability_id}", tags=["planning"])
def planning_capability_action(
    capability_id: str,
    registry: Annotated[CapabilityRegistry, Depends(capabilities)],
    version: int | None = None,
) -> Response:
    return Response(
        content=capability_action(registry.get(capability_id, version)),
        media_type="text/plain",
    )


@router.get("/planning/predicates", tags=["planning"])
def planning_predicates(
    registry: Annotated[CapabilityRegistry, Depends(capabilities)],
) -> list[str]:
    return predicates_list(registry)
