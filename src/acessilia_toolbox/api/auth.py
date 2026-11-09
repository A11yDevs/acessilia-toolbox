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
from typing import Annotated

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from acessilia_toolbox.core.errors import AuthorizationError, ConfigurationError

LOG = logging.getLogger(__name__)

# Read once at import: the key is fixed for the life of the process, and every
# request compares against it. The opt-out flag below is read on each call
# instead, because only create_app consults it, once per application; tests
# set it per test with monkeypatch.setenv, and patch the key on this module.
TOOLBOX_API_KEY = os.getenv("TOOLBOX_API_KEY", "")
ALLOW_UNAUTHENTICATED_ENV = "TOOLBOX_ALLOW_UNAUTHENTICATED"


def unauthenticated_allowed() -> bool:
    """True only when running without a key was asked for explicitly."""
    return os.getenv(ALLOW_UNAUTHENTICATED_ENV, "").strip().lower() in {"1", "true", "yes"}


def ensure_key_configured() -> None:
    """Refuse to build an application without a key, unless explicitly allowed.

    Fail closed: a missing key used to leave every endpoint open with only a
    log line, which a forgotten .env turns into an exposed service.
    """
    if not TOOLBOX_API_KEY and not unauthenticated_allowed():
        raise ConfigurationError(
            "TOOLBOX_API_KEY is not set. Set it, or set "
            f"{ALLOW_UNAUTHENTICATED_ENV}=1 to run without authentication "
            "(local development only)."
        )


bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="ApiKeyAuth",
    description=(
        "API key configured by TOOLBOX_API_KEY. Send Authorization: Bearer <token>. "
        "Without the key the server refuses to start unless "
        "TOOLBOX_ALLOW_UNAUTHENTICATED=1 (local development only)."
    ),
)


async def require_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> None:
    """Verify the request carries a valid API key when one is configured.

    An empty TOOLBOX_API_KEY only reaches this point when the application was
    started with TOOLBOX_ALLOW_UNAUTHENTICATED=1; requests then pass through.
    """
    if not TOOLBOX_API_KEY:
        return

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid token",
        )

    # Constant-time comparison: the time taken must not reveal how much of the
    # token matched.
    if not hmac.compare_digest(
        credentials.credentials.encode("utf-8"), TOOLBOX_API_KEY.encode("utf-8")
    ):
        raise AuthorizationError("Invalid API key")
