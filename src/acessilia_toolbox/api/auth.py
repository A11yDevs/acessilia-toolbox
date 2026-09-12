"""API Key authentication for REST endpoints.

If TOOLBOX_API_KEY is empty or unset, authentication is disabled and all
requests pass through. When set, every request (except /v1/health) must
carry an Authorization: Bearer <token> header matching the configured key.
"""

from __future__ import annotations

import logging
import os

from fastapi import Header, HTTPException, status

from acessilia_toolbox.core.errors import AuthorizationError

LOG = logging.getLogger(__name__)

TOOLBOX_API_KEY = os.getenv("TOOLBOX_API_KEY", "")


async def require_api_key(authorization: str | None = Header(None)) -> None:
    """Verify the request carries a valid API key when one is configured.

    If TOOLBOX_API_KEY is empty, authentication is skipped entirely.
    """
    if not TOOLBOX_API_KEY:
        return

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid token",
        )

    token = authorization.removeprefix("Bearer ")
    if token != TOOLBOX_API_KEY:
        raise AuthorizationError("Invalid API key")
