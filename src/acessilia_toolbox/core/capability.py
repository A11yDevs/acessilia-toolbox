"""Capability manifests and their registry.

A manifest is the single source from which the REST facade, MCP metadata,
PDDL fragments, contract tests and documentation are all derived.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from acessilia_toolbox.core.errors import CapabilityNotFoundError, ConfigurationError

CAPABILITY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+$")
PREDICATE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilityIO(StrictModel):
    # Aliased because `schema` shadows a BaseModel attribute in Pydantic.
    schema_ref: str = Field(alias="schema", min_length=1)
    media_types: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CapabilityExecution(StrictModel):
    deterministic: bool = True
    idempotent: bool = True
    cacheable: bool = True
    timeout_hint_seconds: int = Field(default=120, gt=0)


class CapabilitySemantics(StrictModel):
    """Planning-level world predicates, not request validation."""

    requires: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)

    @field_validator("requires", "produces")
    @classmethod
    def _validate_predicates(cls, value: list[str]) -> list[str]:
        invalid = [item for item in value if not PREDICATE_PATTERN.match(item)]
        if invalid:
            raise ValueError(f"invalid PDDL predicate names: {invalid}")
        return value


class CapabilityProviderBinding(StrictModel):
    id: str = Field(min_length=1)
    version: str | None = None


class CapabilityManifest(StrictModel):
    id: str
    version: int = Field(ge=1)
    description: str = Field(min_length=1)
    input: CapabilityIO
    output: CapabilityIO
    execution: CapabilityExecution = Field(default_factory=CapabilityExecution)
    semantics: CapabilitySemantics = Field(default_factory=CapabilitySemantics)
    providers: list[CapabilityProviderBinding] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not CAPABILITY_ID_PATTERN.match(value):
            raise ValueError(
                f"'{value}' is not a hierarchical, dot-separated lowercase identifier"
            )
        return value

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"


class CapabilityRegistry:
    """In-memory index of the capabilities this deployment exposes."""

    def __init__(self, manifests: list[CapabilityManifest] | None = None) -> None:
        self._by_key: dict[str, CapabilityManifest] = {}
        for manifest in manifests or []:
            self.register(manifest)

    def register(self, manifest: CapabilityManifest) -> None:
        if manifest.key in self._by_key:
            raise ConfigurationError(
                f"duplicate capability {manifest.key}", capability=manifest.key
            )
        self._by_key[manifest.key] = manifest

    @classmethod
    def from_directory(cls, directory: Path) -> CapabilityRegistry:
        if not directory.is_dir():
            raise ConfigurationError(
                f"capability directory not found: {directory}", path=str(directory)
            )
        registry = cls()
        for path in sorted(directory.glob("*.y*ml")):
            registry.register(load_manifest(path))
        return registry

    def get(self, capability_id: str, version: int | None = None) -> CapabilityManifest:
        if version is not None:
            try:
                return self._by_key[f"{capability_id}@{version}"]
            except KeyError:
                raise CapabilityNotFoundError(
                    f"unknown capability {capability_id}@{version}",
                    capability=capability_id,
                    version=version,
                ) from None

        candidates = [m for m in self._by_key.values() if m.id == capability_id]
        if not candidates:
            raise CapabilityNotFoundError(
                f"unknown capability {capability_id}", capability=capability_id
            )
        return max(candidates, key=lambda manifest: manifest.version)

    def manifests(self) -> list[CapabilityManifest]:
        return sorted(self._by_key.values(), key=lambda m: (m.id, m.version))

    def ids(self) -> list[str]:
        return sorted({manifest.id for manifest in self._by_key.values()})

    def __len__(self) -> int:
        return len(self._by_key)

    def __contains__(self, key: object) -> bool:
        return key in self._by_key


def load_manifest(path: Path) -> CapabilityManifest:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML in {path}: {exc}", path=str(path)) from exc

    if not isinstance(raw, dict):
        raise ConfigurationError(
            f"capability manifest must be a mapping: {path}", path=str(path)
        )

    try:
        return CapabilityManifest.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(
            f"invalid capability manifest {path}: {exc}", path=str(path)
        ) from exc
