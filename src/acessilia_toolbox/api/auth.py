"""API Key authentication for REST endpoints.

When TOOLBOX_API_KEY is set, every request (except /v1/health) must carry an
Authorization: Bearer <token> header matching it. The application refuses to
start without a key unless TOOLBOX_ALLOW_UNAUTHENTICATED=1 is set explicitly
(local development only); see ``api.app.create_app``.
"""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import Header, HTTPException, status

from acessilia_toolbox.core.errors import AuthorizationError

LOG = logging.getLogger(__name__)

TOOLBOX_API_KEY = os.getenv("TOOLBOX_API_KEY", "")
ALLOW_UNAUTHENTICATED_ENV = "TOOLBOX_ALLOW_UNAUTHENTICATED"


def unauthenticated_allowed() -> bool:
    """True only when running without a key was asked for explicitly."""
    return os.getenv(ALLOW_UNAUTHENTICATED_ENV, "").strip().lower() in {"1", "true", "yes"}


async def require_api_key(authorization: str | None = Header(None)) -> None:
    """Verify the request carries a valid API key when one is configured.

    An empty TOOLBOX_API_KEY only reaches this point when the application was
    started with TOOLBOX_ALLOW_UNAUTHENTICATED=1; requests then pass through.
    """
    if not TOOLBOX_API_KEY:
        return

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid token",
        )

    token = authorization.removeprefix("Bearer ")
    # Constant-time comparison: the time taken must not reveal how much of the
    # token matched.
    if not hmac.compare_digest(token.encode("utf-8"), TOOLBOX_API_KEY.encode("utf-8")):
        raise AuthorizationError("Invalid API key")
