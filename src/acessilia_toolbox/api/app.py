"""Application factory and configuration."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from acessilia_toolbox import __version__
from acessilia_toolbox.api.rest import router
from acessilia_toolbox.core.artifact import ArtifactStore, ExecutionCache
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import ToolboxError, ConfigurationError
from acessilia_toolbox.core.executor import CapabilityExecutor
from acessilia_toolbox.core.provider import ProviderRegistry
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.cache import create_cache
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
    app = FastAPI(
        title="Acessilia Toolbox",
        version=__version__,
        description=DESCRIPTION,
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
    )

    app.state.capabilities = capabilities or CapabilityRegistry.from_directory(
        Path(os.getenv("TOOLBOX_CAPABILITIES_DIR", DEFAULT_CAPABILITIES_DIR))
    )
    app.state.providers = providers or ProviderRegistry.from_file(
        Path(os.getenv("TOOLBOX_PROVIDERS_CONFIG", DEFAULT_PROVIDERS_CONFIG))
    )
    app.state.store = store if store is not None else _store_from(app.state.providers)
    app.state.cache = cache if cache is not None else _cache_from(app.state.providers)
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

    app.include_router(router)
    return app


def _store_from(providers: ProviderRegistry) -> ArtifactStore | None:
    candidates = providers.for_capability("artifact.store")
    if not candidates:
        return None
    descriptor = candidates[0]
    if _is_unresolved(dict(descriptor.config)):
        return None
    try:
        store = _create_store(descriptor)
    except ConfigurationError as exc:
        LOG.warning("artifact store disabled: %s", exc)
        return None
    return store if isinstance(store, ArtifactStore) else None


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
