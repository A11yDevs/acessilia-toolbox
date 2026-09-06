"""Provider descriptors, adapter protocol and registry.

Providers are external and replaceable. The toolbox holds their topology
and binds them to capabilities; it never embeds their runtimes.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from acessilia_toolbox.core.errors import (
    ConfigurationError,
    ProviderNotBoundError,
    ProviderNotFoundError,
)

ENV_PLACEHOLDER = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

Transport = Literal["http", "s3", "redis", "in_process"]

# Config keys whose values must never reach a response or a log line.
SECRET_KEY_HINTS = ("key", "secret", "token", "password", "credential")
REDACTED = "***"


class ProviderDescriptor(BaseModel):
    """Static registration data for one provider."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    version: str = "unknown"
    transport: Transport = "http"
    endpoint: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    health_path: str = "/health"
    media_types: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=600.0, gt=0)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capabilities")
    @classmethod
    def _require_capabilities(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("provider must declare at least one capability")
        return value

    def implements(self, capability_id: str) -> bool:
        return capability_id in self.capabilities

    def public_payload(self) -> dict[str, Any]:
        """Descriptor view safe to expose over REST/MCP."""
        payload = self.model_dump(mode="json", exclude={"config"})
        payload["config"] = {
            key: (REDACTED if _is_secret(key) else value)
            for key, value in self.config.items()
        }
        return payload


class ProviderHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    healthy: bool
    version: str | None = None
    detail: str | None = None
    checked_at: datetime


@runtime_checkable
class ProviderAdapter(Protocol):
    """Contract every provider adapter fulfils.

    Adapters stay thin: transport plus shape mapping. Normalization into the
    canonical structure belongs to the core.
    """

    descriptor: ProviderDescriptor

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> Any: ...

    def versions(self) -> dict[str, str]:
        """Provider and component versions, queried before execution so a cache
        lookup can account for an upgrade."""
        ...

    def health(self) -> ProviderHealth: ...


class ProviderRegistry:
    """Index of registered providers and the capabilities they implement."""

    def __init__(self, descriptors: Sequence[ProviderDescriptor] | None = None) -> None:
        self._by_id: dict[str, ProviderDescriptor] = {}
        for descriptor in descriptors or []:
            self.register(descriptor)

    def register(self, descriptor: ProviderDescriptor) -> None:
        if descriptor.id in self._by_id:
            raise ConfigurationError(
                f"duplicate provider {descriptor.id}", provider=descriptor.id
            )
        self._by_id[descriptor.id] = descriptor

    @classmethod
    def from_file(cls, path: Path) -> ProviderRegistry:
        if not path.is_file():
            raise ConfigurationError(
                f"provider configuration not found: {path}", path=str(path)
            )
        try:
            raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigurationError(
                f"invalid YAML in {path}: {exc}", path=str(path)
            ) from exc
        return cls.from_mapping(raw or {}, source=str(path))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str = "<memory>") -> ProviderRegistry:
        entries = raw.get("providers")
        if not isinstance(entries, list):
            raise ConfigurationError(
                f"provider configuration must contain a 'providers' list: {source}",
                source=source,
            )

        registry = cls()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ConfigurationError(
                    f"each provider entry must be a mapping: {source}", source=source
                )
            try:
                descriptor = ProviderDescriptor.model_validate(expand_env(entry))
            except ValidationError as exc:
                raise ConfigurationError(
                    f"invalid provider entry in {source}: {exc}", source=source
                ) from exc
            registry.register(descriptor)
        return registry

    def get(self, provider_id: str) -> ProviderDescriptor:
        try:
            return self._by_id[provider_id]
        except KeyError:
            raise ProviderNotFoundError(
                f"unknown provider {provider_id}", provider=provider_id
            ) from None

    def for_capability(self, capability_id: str) -> list[ProviderDescriptor]:
        return [d for d in self.descriptors() if d.implements(capability_id)]

    def resolve(self, capability_id: str, provider_id: str | None = None) -> ProviderDescriptor:
        """Pick a provider for a capability.

        Selection is technical only. Choosing between providers on semantic
        grounds is the agent's call, so an ambiguous request is not guessed.
        """
        if provider_id is not None:
            descriptor = self.get(provider_id)
            if not descriptor.implements(capability_id):
                raise ProviderNotBoundError(
                    f"provider {provider_id} does not implement {capability_id}",
                    provider=provider_id,
                    capability=capability_id,
                )
            return descriptor

        candidates = self.for_capability(capability_id)
        if not candidates:
            raise ProviderNotFoundError(
                f"no provider implements {capability_id}", capability=capability_id
            )
        if len(candidates) > 1:
            raise ProviderNotBoundError(
                f"{capability_id} has several providers; name one explicitly",
                capability=capability_id,
                providers=[d.id for d in candidates],
            )
        return candidates[0]

    def descriptors(self) -> list[ProviderDescriptor]:
        return sorted(self._by_id.values(), key=lambda d: d.id)

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, provider_id: object) -> bool:
        return provider_id in self._by_id


def _is_secret(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in SECRET_KEY_HINTS)


def expand_env(value: Any) -> Any:
    """Resolve `${VAR}` placeholders. Missing variables are kept as-is so the
    registry stays loadable even when optional providers are not configured;
    they will fail naturally when actually used.
    """
    if isinstance(value, str):

        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            resolved = os.environ.get(name)
            return resolved if resolved is not None else match.group(0)

        return ENV_PLACEHOLDER.sub(_replace, value)
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    return value
