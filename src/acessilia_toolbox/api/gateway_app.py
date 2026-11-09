"""The Toolbox in VLM gateway mode.

The same package as the full Toolbox, started with another factory: it serves
only the public health route and the VLM chat gateway (``/v1/vlm``), with a
provider configuration that lists the model server. Run next to the model
server, it is the one client of that server, and the Toolboxes of every
environment point their VLM provider at it, so one limiter holds all of their
requests to the server's parallel slots.

    uvicorn acessilia_toolbox.api.gateway_app:create_gateway_app --factory

See ``deploy/vlm-gateway/`` for a deployment next to llama-server.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from acessilia_toolbox import __version__
from acessilia_toolbox.api import auth
from acessilia_toolbox.api.rest import health
from acessilia_toolbox.api.schemas import HealthResponse
from acessilia_toolbox.api.vlm_gateway import vlm_router
from acessilia_toolbox.core.errors import ToolboxError
from acessilia_toolbox.core.provider import ProviderRegistry

LOG = logging.getLogger(__name__)

DEFAULT_PROVIDERS_CONFIG = Path("providers-config.yaml")


def create_gateway_app(providers: ProviderRegistry | None = None) -> FastAPI:
    auth.ensure_key_configured()

    app = FastAPI(
        title="Acessilia VLM gateway",
        version=__version__,
        description="OpenAI-compatible chat relay to the vision-language model "
        "servers, under one limiter per provider.",
        # Only the three routes the deployment documents: no API docs on a
        # relay that sits on the network next to the model server.
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.providers = providers or ProviderRegistry.from_file(
        Path(os.getenv("TOOLBOX_PROVIDERS_CONFIG", DEFAULT_PROVIDERS_CONFIG))
    )
    @app.exception_handler(ToolboxError)
    async def _toolbox_error(_: Request, error: ToolboxError) -> JSONResponse:
        return JSONResponse(status_code=error.http_status, content=error.to_payload())

    # The liveness route only, not the whole public router: its /v1/ready
    # checks structure extraction and storage, which a gateway does not have,
    # and a route added there later must not reach the relay unannounced.
    app.add_api_route(
        "/v1/health", health, methods=["GET"], response_model=HealthResponse, tags=["health"]
    )
    app.include_router(vlm_router)

    if not auth.TOOLBOX_API_KEY:
        LOG.warning(
            "%s=1 and no TOOLBOX_API_KEY: the VLM gateway is unauthenticated. "
            "Never use this outside local development.",
            auth.ALLOW_UNAUTHENTICATED_ENV,
        )

    return app
