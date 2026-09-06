"""REST facade over the capability registry."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from acessilia_toolbox import __version__
from acessilia_toolbox.api.schemas import (
    ArtifactRef,
    CapabilityDetail,
    CapabilitySummary,
    ErrorResponse,
    ExecutionResponse,
    HealthResponse,
)
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import InvalidInputError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.provider import ProviderHealth, ProviderRegistry
from acessilia_toolbox.providers import create_adapter

router = APIRouter(prefix="/v1")

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


@router.get("/health", response_model=HealthResponse, tags=["health"])
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
    file: Annotated[UploadFile, File(description="Document to process")],
    provider: Annotated[str | None, Form()] = None,
    parameters: Annotated[str | None, Form(description="JSON object")] = None,
    language: Annotated[str, Form()] = "pt-BR",
    version: Annotated[int | None, Form()] = None,
) -> ExecutionResponse:
    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise InvalidInputError(
            f"upload exceeds {MAX_UPLOAD_BYTES} bytes", capability=capability_id
        )

    result = runner.execute(
        capability_id,
        payload,
        filename=file.filename or "upload",
        media_type=file.content_type or "application/octet-stream",
        provider_id=provider,
        capability_version=version,
        parameters=_parse_parameters(parameters),
        language=language,
    )

    document = json.dumps(result.document, ensure_ascii=False).encode("utf-8")
    return ExecutionResponse(
        status=result.status,
        capability=result.capability,
        provider=result.provider,
        artifacts=[
            ArtifactRef(artifact_id=fingerprint_bytes(document), size=len(document))
        ],
        document=result.document,
        provenance=result.provenance,
    )


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
