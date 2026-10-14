"""Application factory and configuration."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import BoundedSemaphore

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from acessilia_toolbox import __version__
from acessilia_toolbox.api import auth
from acessilia_toolbox.api.rest import MAX_UPLOAD_BYTES, dataset_router, public_router, router
from acessilia_toolbox.api.upload_limit import UploadLimitMiddleware
from acessilia_toolbox.core.artifact import ArtifactStore, ExecutionCache
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import ConfigurationError, ToolboxError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers import configure_dataset_mirroring, create_adapter
from acessilia_toolbox.providers.cache import create_cache
from acessilia_toolbox.providers.storage import FailoverArtifactStore
from acessilia_toolbox.providers.storage import create_artifact_store as _create_store

LOG = logging.getLogger(__name__)

DEFAULT_CAPABILITIES_DIR = Path("capabilities")
DEFAULT_PROVIDERS_CONFIG = Path("providers-config.yaml")

DESCRIPTION = """
Deterministic, stateless capability layer for agentic systems.

Capabilities describe what can be done; providers implement it. The toolbox
executes and normalizes, while goals, planning and provider choice stay with
the agentic core.
""".strip()


def create_app(
    capabilities: CapabilityRegistry | None = None,
    providers: ProviderRegistry | None = None,
    *,
    cache: ExecutionCache | None = None,
    store: ArtifactStore | None = None,
) -> FastAPI:
    # Fail closed: a missing key used to leave every endpoint open with only a
    # log line, which a forgotten .env turns into an exposed service.
    if not auth.TOOLBOX_API_KEY and not auth.unauthenticated_allowed():
        raise ConfigurationError(
            "TOOLBOX_API_KEY is not set. Set it, or set "
            f"{auth.ALLOW_UNAUTHENTICATED_ENV}=1 to run without authentication "
            "(local development only)."
        )

    app = FastAPI(
        title="Acessilia Toolbox",
        version=__version__,
        description=DESCRIPTION,
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
    )

    app.add_middleware(UploadLimitMiddleware, max_bytes=MAX_UPLOAD_BYTES + 1024 * 1024)

    app.state.capabilities = capabilities or CapabilityRegistry.from_directory(
        Path(os.getenv("TOOLBOX_CAPABILITIES_DIR", DEFAULT_CAPABILITIES_DIR))
    )
    app.state.providers = providers or ProviderRegistry.from_file(
        Path(os.getenv("TOOLBOX_PROVIDERS_CONFIG", DEFAULT_PROVIDERS_CONFIG))
    )
    app.state.store = store if store is not None else _store_from(app.state.providers)
    app.state.cache = cache if cache is not None else _cache_from(app.state.providers)

    # Inject store/cache into dataset adapters for mirroring support.
    configure_dataset_mirroring(store=app.state.store, cache=app.state.cache)

    app.state.execution_slots = BoundedSemaphore(
        max(1, int(os.getenv("TOOLBOX_MAX_CONCURRENT_EXECUTIONS", "4")))
    )
    app.state.executor = CapabilityExecutor(
        app.state.capabilities,
        app.state.providers,
        create_adapter,
        cache=app.state.cache,
        store=app.state.store,
    )

    @app.exception_handler(ToolboxError)
    async def _toolbox_error(_: Request, error: ToolboxError) -> JSONResponse:
        return JSONResponse(status_code=error.http_status, content=error.to_payload())

    app.include_router(public_router)
    app.include_router(router)
    app.include_router(dataset_router)

    if not auth.TOOLBOX_API_KEY:
        LOG.warning(
            "%s=1 and no TOOLBOX_API_KEY: all REST endpoints are unauthenticated. "
            "Never use this outside local development.",
            auth.ALLOW_UNAUTHENTICATED_ENV,
        )

    return app


def _store_from(providers: ProviderRegistry) -> ArtifactStore | None:
    candidates = providers.for_capability("artifact.store")
    if not candidates:
        return None

    def first_available(descriptors: list[ProviderDescriptor]) -> ArtifactStore | None:
        for descriptor in descriptors:
            if _is_unresolved({**descriptor.config, "endpoint": descriptor.endpoint or ""}):
                continue
            try:
                store = _create_store(descriptor)
            except (ConfigurationError, OSError) as exc:
                LOG.warning("artifact store provider %s disabled: %s", descriptor.id, exc)
                continue
            if isinstance(store, ArtifactStore):
                return store
        return None

    primary = first_available([d for d in candidates if d.transport == "s3"])
    fallback = first_available([d for d in candidates if d.transport != "s3"])
    if primary is not None and fallback is not None:
        return FailoverArtifactStore(primary, fallback)
    return primary or fallback


def _cache_from(providers: ProviderRegistry) -> ExecutionCache | None:
    candidates = [d for d in providers.descriptors() if d.transport == "redis"]
    if not candidates:
        return None
    descriptor = candidates[0]
    if _is_unresolved({"endpoint": descriptor.endpoint or ""}):
        return None
    try:
        return create_cache(descriptor)
    except ConfigurationError as exc:
        LOG.warning("execution cache disabled: %s", exc)
        return None


def _is_unresolved(values: dict[str, object]) -> bool:
    return any("${" in str(v) for v in values.values())
