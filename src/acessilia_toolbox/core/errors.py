"""Normalized error taxonomy.

Providers fail in provider-specific ways. Consumers must not have to learn
each of them, so every failure is mapped onto a stable code here.
"""

from __future__ import annotations

from typing import Any


class ToolboxError(Exception):
    """Base class for every error the toolbox reports to consumers."""

    code = "internal_error"
    http_status = 500

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class ConfigurationError(ToolboxError):
    """Raised when manifests or provider topology cannot be loaded."""

    code = "configuration_error"
    http_status = 500


class CapabilityNotFoundError(ToolboxError):
    code = "capability_not_found"
    http_status = 404


class ProviderNotFoundError(ToolboxError):
    code = "provider_not_found"
    http_status = 404


class ProviderNotBoundError(ToolboxError):
    """The provider exists but does not implement the requested capability."""

    code = "provider_not_bound"
    http_status = 409


class InvalidInputError(ToolboxError):
    code = "invalid_input"
    http_status = 400


class UnsupportedMediaTypeError(ToolboxError):
    code = "unsupported_media_type"
    http_status = 415


class ArtifactNotFoundError(ToolboxError):
    code = "artifact_not_found"
    http_status = 404


class AuthorizationError(ToolboxError):
    """The request lacks valid authentication credentials."""

    code = "authorization_failed"
    http_status = 401


class ProviderUnavailableError(ToolboxError):
    code = "provider_unavailable"
    http_status = 503


class ProviderTimeoutError(ToolboxError):
    code = "provider_timeout"
    http_status = 504


class ProviderExecutionError(ToolboxError):
    """The provider was reached and refused or failed the work."""

    code = "provider_execution_failed"
    http_status = 502
