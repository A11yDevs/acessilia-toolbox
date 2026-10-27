"""REST facade over the capability registry."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from typing import Annotated, Any, BinaryIO
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from acessilia_toolbox import __version__
from acessilia_toolbox.api.auth import require_api_key
from acessilia_toolbox.api.schemas import (
    CapabilityDetail,
    CapabilitySummary,
    ErrorResponse,
    ExecutionResponse,
    HealthResponse,
    ReadinessResponse,
    UploadLimitResponse,
)
from acessilia_toolbox.core.artifact import ArtifactRef
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import (
    DatasetNotFoundError,
    InvalidInputError,
    ProviderUnavailableError,
    UploadTooLargeError,
)
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

# Dataset router: authenticated dataset access endpoints.
dataset_router = APIRouter(
    prefix="/v1/datasets",
    dependencies=[Depends(require_api_key)],
    tags=["datasets"],
)

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    415: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}

UPLOAD_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **ERROR_RESPONSES,
    413: {"model": ErrorResponse | UploadLimitResponse},
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
    return HealthResponse(
        status="healthy",
        version=__version__,
        git_commit=os.getenv("GIT_COMMIT", ""),
        image_tag=os.getenv("IMAGE_TAG", ""),
    )


@public_router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "Service unavailable"}},
    tags=["health"],
)
def ready(request: Request, provider: str | None = None) -> JSONResponse:
    from acessilia_toolbox.providers.storage import storage_ready

    checks = {"catalog": False, "structure": False, "storage": False}
    try:
        request.app.state.capabilities.get("document.structure.extract")
        checks["catalog"] = True
        descriptor = request.app.state.providers.resolve(
            "document.structure.extract",
            provider or os.getenv("TOOLBOX_READY_PROVIDER", "docling")
        )
        if descriptor.transport == "http" and descriptor.endpoint:
            with httpx.Client(base_url=descriptor.endpoint, timeout=2.0) as client:
                checks["structure"] = client.get(descriptor.health_path).status_code == 200
        else:
            checks["structure"] = create_adapter(descriptor).health().healthy
    except Exception:
        pass
    with contextlib.suppress(Exception):
        checks["storage"] = storage_ready(request.app.state.store)
    healthy = all(checks.values())
    return JSONResponse(
        {"status": "ready" if healthy else "unavailable", "checks": checks},
        status_code=200 if healthy else 503,
    )


@router.get("/capabilities", response_model=list[CapabilitySummary], tags=["capabilities"])
def list_capabilities(
    registry: Annotated[CapabilityRegistry, Depends(capabilities)],
    provider_registry: Annotated[ProviderRegistry, Depends(providers)],
) -> list[CapabilitySummary]:
    latest = {manifest.key for manifest in registry.latest()}
    return [
        CapabilitySummary.of(
            manifest,
            _registered(provider_registry, manifest.id),
            latest=manifest.key in latest,
        )
        for manifest in registry.manifests()
    ]


@router.get(
    "/capabilities/{capability_id}",
    response_model=CapabilityDetail,
    responses=ERROR_RESPONSES,
    tags=["capabilities"],
)
def get_capability(
    capability_id: str,
    registry: Annotated[CapabilityRegistry, Depends(capabilities)],
    provider_registry: Annotated[ProviderRegistry, Depends(providers)],
    version: int | None = None,
) -> CapabilityDetail:
    manifest = registry.get(capability_id, version)
    return CapabilityDetail.of(
        manifest,
        _registered(provider_registry, manifest.id),
        latest=manifest.version == registry.get(capability_id).version,
    )


def _registered(registry: ProviderRegistry, capability_id: str) -> list[str]:
    """Providers registered here for a capability; what a client can execute."""
    return [descriptor.id for descriptor in registry.for_capability(capability_id)]


@router.post(
    "/capabilities/{capability_id}:execute",
    response_model=ExecutionResponse,
    responses=UPLOAD_ERROR_RESPONSES,
    tags=["capabilities"],
)
async def execute_capability(
    capability_id: str,
    runner: Annotated[CapabilityExecutor, Depends(executor)],
    request: Request,
    file: Annotated[UploadFile | None, File(description="Document to process")] = None,
    artifact_id: Annotated[str | None, Form(description="Stored input")] = None,
    provider: Annotated[str | None, Form()] = None,
    parameters: Annotated[str | None, Form(description="JSON object")] = None,
    language: Annotated[str, Form()] = "pt-BR",
    version: Annotated[int | None, Form()] = None,
    no_cache: Annotated[bool, Form(description="Skip execution cache")] = False,
) -> ExecutionResponse:
    slots = request.app.state.execution_slots
    if not slots.acquire(blocking=False):
        raise ProviderUnavailableError("execution capacity exhausted; retry later")
    try:
        with contextlib.ExitStack() as stack:
            return await _execute_with_input(
                runner, stack, capability_id, file, artifact_id, provider, parameters,
                language, version, no_cache,
            )
    finally:
        slots.release()


async def _execute_with_input(
    runner: CapabilityExecutor, stack: contextlib.ExitStack, capability_id: str,
    file: UploadFile | None, artifact_id: str | None, provider: str | None,
    parameters: str | None, language: str, version: int | None, no_cache: bool,
) -> ExecutionResponse:
    payload: bytes | BinaryIO
    if file is not None and not artifact_id:
        await _check_upload_stream(file)
        payload, filename, media_type = (
            file.file,
            file.filename or "upload",
            file.content_type or "application/octet-stream",
        )
    elif artifact_id and file is None:
        payload = stack.enter_context(tempfile.TemporaryFile())  # noqa: SIM115
        ref = await run_in_threadpool(
            runner.retrieve_artifact_to, artifact_id, payload, max_bytes=MAX_UPLOAD_BYTES
        )
        filename, media_type = ref.filename or "artifact", ref.media_type
    else:
        payload, filename, media_type = await _resolve_input(runner, file, artifact_id)
    if isinstance(payload, bytes) and len(payload) > MAX_UPLOAD_BYTES:
        raise InvalidInputError(
            f"upload exceeds {MAX_UPLOAD_BYTES} bytes", capability=capability_id
        )

    result = await run_in_threadpool(
        runner.execute,
        capability_id,
        payload,
        filename=filename,
        media_type=media_type,
        provider_id=provider,
        capability_version=version,
        parameters=_parse_parameters(parameters),
        language=language,
        no_cache=no_cache,
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
    responses=UPLOAD_ERROR_RESPONSES,
    tags=["artifacts"],
)
async def store_artifact(
    runner: Annotated[CapabilityExecutor, Depends(executor)],
    file: Annotated[UploadFile, File(description="Content to store")],
    request: Request,
) -> ArtifactRef:
    slots = request.app.state.execution_slots
    if not slots.acquire(blocking=False):
        raise ProviderUnavailableError("execution capacity exhausted")
    try:
        await _check_upload_stream(file)
        return await run_in_threadpool(
            runner.store_artifact_stream,
            file.file,
            media_type=file.content_type or "application/octet-stream",
            filename=file.filename,
        )
    finally:
        slots.release()


@router.get("/artifacts/{artifact_id}", responses=ERROR_RESPONSES, tags=["artifacts"])
def retrieve_artifact(
    artifact_id: str,
    runner: Annotated[CapabilityExecutor, Depends(executor)],
) -> Response:
    payload, ref = runner.retrieve_artifact(artifact_id)
    headers = {"ETag": ref.artifact_id}
    if ref.filename:
        clean = "".join(char for char in ref.filename if ord(char) >= 32 and ord(char) != 127)
        encoded = quote(clean, safe="")
        fallback = (
            "".join(char for char in clean if ord(char) < 127 and char not in '"/\\') or "artifact"
        )
        headers["Content-Disposition"] = (
            f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
        )
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
    return runner.stat_artifact(artifact_id)


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


async def _check_upload_stream(file: UploadFile) -> None:
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise UploadTooLargeError(f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise UploadTooLargeError(f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
    if not size:
        raise InvalidInputError("empty payload")
    await file.seek(0)


async def _read_upload(file: UploadFile) -> bytes:
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise UploadTooLargeError(f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise UploadTooLargeError(f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
    return payload


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
            await _read_upload(file),
            file.filename or "upload",
            file.content_type or "application/octet-stream",
        )
    if artifact_id:
        payload, ref = await run_in_threadpool(
            runner.retrieve_artifact, artifact_id, max_bytes=MAX_UPLOAD_BYTES
        )
        return payload, ref.filename or "artifact", ref.media_type
    raise InvalidInputError("a file or an artifact_id is required")


# ---------------------------------------------------------------------------
# Dataset endpoints
# ---------------------------------------------------------------------------


def _resolve_dataset_provider(
    runner: CapabilityExecutor,
    providers: ProviderRegistry,
    dataset_id: str,
) -> str:
    """Pick the dataset provider that actually knows ``dataset_id``.

    Each dataset belongs to exactly one provider (github or huggingface).
    Tries each candidate and falls back to the first one on unknown datasets.
    """

    candidates = [d.id for d in providers.for_capability("dataset.describe")]
    for provider_id in candidates:
        try:
            runner.execute(
                "dataset.describe",
                b"{}",
                filename="query.json",
                media_type="application/json",
                provider_id=provider_id,
                parameters={"dataset_id": dataset_id},
            )
            return provider_id
        except Exception as exc:
            if not isinstance(exc, DatasetNotFoundError):
                raise
    raise DatasetNotFoundError(f"unknown dataset '{dataset_id}'", dataset=dataset_id)


@dataset_router.get("", response_model=list[dict[str, Any]])
def list_datasets(
    runner: Annotated[CapabilityExecutor, Depends(executor)],
    providers: Annotated[ProviderRegistry, Depends(providers)],
) -> list[dict[str, Any]]:
    """List all datasets available through the toolbox (all providers)."""
    datasets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for provider_id in (d.id for d in providers.for_capability("dataset.list")):
        result = runner.execute(
            "dataset.list",
            b"{}",
            filename="query.json",
            media_type="application/json",
            provider_id=provider_id,
        )
        for ds in result.document:
            if ds.get("id") not in seen:
                seen.add(ds.get("id"))
                datasets.append(ds)
    return datasets


@dataset_router.get("/{dataset_id}", response_model=dict[str, Any])
def describe_dataset(
    dataset_id: str,
    runner: Annotated[CapabilityExecutor, Depends(executor)],
    providers: Annotated[ProviderRegistry, Depends(providers)],
    revision: str | None = Query(None, description="Git/HF revision to pin"),
) -> dict[str, Any]:
    """Get detailed metadata about a specific dataset."""
    provider_id = _resolve_dataset_provider(runner, providers, dataset_id)
    result = runner.execute(
        "dataset.describe",
        b"{}",
        filename="query.json",
        media_type="application/json",
        provider_id=provider_id,
        parameters={"dataset_id": dataset_id, "revision": revision},
    )
    return result.document  # type: ignore[no-any-return]


@dataset_router.get("/{dataset_id}/splits", response_model=list[dict[str, Any]])
def list_dataset_splits(
    dataset_id: str,
    runner: Annotated[CapabilityExecutor, Depends(executor)],
    providers: Annotated[ProviderRegistry, Depends(providers)],
    revision: str | None = Query(None, description="Git/HF revision to pin"),
) -> list[dict[str, Any]]:
    """List available splits for a dataset."""
    provider_id = _resolve_dataset_provider(runner, providers, dataset_id)
    result = runner.execute(
        "dataset.list_splits",
        b"{}",
        filename="query.json",
        media_type="application/json",
        provider_id=provider_id,
        parameters={"dataset_id": dataset_id, "revision": revision},
    )
    return result.document  # type: ignore[no-any-return]


@dataset_router.get("/{dataset_id}/splits/{split}/items", response_model=list[dict[str, Any]])
def list_dataset_items(
    dataset_id: str,
    split: str,
    runner: Annotated[CapabilityExecutor, Depends(executor)],
    providers: Annotated[ProviderRegistry, Depends(providers)],
    revision: str | None = Query(None, description="Git/HF revision to pin"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List items in a dataset split with pagination."""
    provider_id = _resolve_dataset_provider(runner, providers, dataset_id)
    result = runner.execute(
        "dataset.list_items",
        b"{}",
        filename="query.json",
        media_type="application/json",
        provider_id=provider_id,
        parameters={
            "dataset_id": dataset_id,
            "split": split,
            "revision": revision,
            "limit": limit,
            "offset": offset,
        },
    )
    return result.document  # type: ignore[no-any-return]


@dataset_router.get(
    "/{dataset_id}/splits/{split}/items/{item_id}",
    response_model=dict[str, Any],
)
def get_dataset_item(
    dataset_id: str,
    split: str,
    item_id: str,
    runner: Annotated[CapabilityExecutor, Depends(executor)],
    providers: Annotated[ProviderRegistry, Depends(providers)],
    revision: str | None = Query(None, description="Git/HF revision to pin"),
) -> dict[str, Any]:
    """Get a full item with artifact references and annotations."""
    provider_id = _resolve_dataset_provider(runner, providers, dataset_id)
    result = runner.execute(
        "dataset.get_item",
        b"{}",
        filename="query.json",
        media_type="application/json",
        provider_id=provider_id,
        parameters={
            "dataset_id": dataset_id,
            "split": split,
            "item_id": item_id,
            "revision": revision,
        },
    )
    return result.document  # type: ignore[no-any-return]


@dataset_router.get(
    "/{dataset_id}/splits/{split}/items/{item_id}/artifacts/{path:path}",
)
def get_dataset_artifact(
    dataset_id: str,
    split: str,
    item_id: str,
    path: str,
    runner: Annotated[CapabilityExecutor, Depends(executor)],
    providers: Annotated[ProviderRegistry, Depends(providers)],
    revision: str | None = Query(None, description="Git/HF revision to pin"),
) -> Response:
    """Retrieve the raw bytes of a dataset artifact."""
    provider_id = _resolve_dataset_provider(runner, providers, dataset_id)
    result = runner.execute(
        "dataset.get_artifact",
        b"{}",
        filename="query.json",
        media_type="application/json",
        provider_id=provider_id,
        parameters={
            "dataset_id": dataset_id,
            "split": split,
            "item_id": item_id,
            "artifact_path": path,
            "revision": revision,
        },
    )
    doc = result.document
    payload = bytes.fromhex(doc["payload"])
    media_type = doc.get("media_type", "application/octet-stream")
    return Response(content=payload, media_type=media_type)


@dataset_router.get(
    "/{dataset_id}/splits/{split}/sample",
    response_model=list[dict[str, Any]],
)
def sample_dataset(
    dataset_id: str,
    split: str,
    runner: Annotated[CapabilityExecutor, Depends(executor)],
    providers: Annotated[ProviderRegistry, Depends(providers)],
    revision: str | None = Query(None, description="Git/HF revision to pin"),
    n: int = Query(5, ge=1, le=100),
) -> list[dict[str, Any]]:
    """Get a random sample of items from a dataset split."""
    provider_id = _resolve_dataset_provider(runner, providers, dataset_id)
    result = runner.execute(
        "dataset.sample",
        b"{}",
        filename="query.json",
        media_type="application/json",
        provider_id=provider_id,
        parameters={"dataset_id": dataset_id, "split": split, "revision": revision, "n": n},
    )
    return result.document  # type: ignore[no-any-return]


@dataset_router.post(
    "/{dataset_id}/sync",
    response_model=list[dict[str, Any]],
)
def sync_dataset(
    dataset_id: str,
    runner: Annotated[CapabilityExecutor, Depends(executor)],
    providers: Annotated[ProviderRegistry, Depends(providers)],
    revision: str | None = Query(None, description="Git/HF revision to pin"),
    split: str | None = Query(None, description="Sync only this split"),
) -> list[dict[str, Any]]:
    """Re-mirror all artifacts for a dataset to the local artifact store.

    Requires mirroring to be enabled in the provider configuration.
    """
    provider_id = _resolve_dataset_provider(runner, providers, dataset_id)
    result = runner.execute(
        "dataset.sync",
        b"{}",
        filename="query.json",
        media_type="application/json",
        provider_id=provider_id,
        parameters={
            "dataset_id": dataset_id,
            "revision": revision,
            "split": split,
        },
    )
    return result.document  # type: ignore[no-any-return]


@router.get("/planning/domain", tags=["planning"])
def planning_domain(
    registry: Annotated[CapabilityRegistry, Depends(capabilities)],
) -> Response:
    return Response(
        content=domain_fragment(registry.latest()),
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
