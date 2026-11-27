"""Version-1 DTOs and one-pass Docstruct normalization, independent of Core state."""

from __future__ import annotations

from typing import Self

from docstruct.text.norm_fix import norm_fix
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from acessilia_toolbox.core.fingerprint import fingerprint_parameters

MAX_TARGETS = 256
MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_ID_LENGTH = 256
MAX_EXECUTION_SECONDS = 5.0


class OperationalDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TextTarget(OperationalDTO):
    id: str = Field(min_length=1, max_length=MAX_ID_LENGTH)
    text: str | None = Field(max_length=MAX_PAYLOAD_BYTES)

    @field_validator("id", "text")
    @classmethod
    def valid_unicode(cls, value: str | None) -> str | None:
        if value is not None:
            # JSON can escape unpaired surrogates, which have no UTF-8 identity.
            value.encode("utf-8")
        return value

    def fingerprint(self) -> str:
        return fingerprint_parameters({"id": self.id, "text": self.text})


class NormalizationRequest(OperationalDTO):
    targets: list[TextTarget] = Field(max_length=MAX_TARGETS)

    @field_validator("targets")
    @classmethod
    def unique_ids(cls, targets: list[TextTarget]) -> list[TextTarget]:
        if len({target.id for target in targets}) != len(targets):
            raise ValueError("target ids must be unique")
        return targets


class NormalizationObservation(TextTarget):
    input_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    processed: bool
    changed: bool


class NormalizationResult(OperationalDTO):
    results: list[NormalizationObservation] = Field(max_length=MAX_TARGETS)
    processed_count: int = Field(ge=0, le=MAX_TARGETS)
    changed_count: int = Field(ge=0, le=MAX_TARGETS)

    @model_validator(mode="after")
    def consistent_counts(self) -> Self:
        if len({item.id for item in self.results}) != len(self.results):
            raise ValueError("result ids must be unique")
        if self.processed_count != sum(item.processed for item in self.results):
            raise ValueError("processed_count does not match observations")
        if self.changed_count != sum(item.changed for item in self.results):
            raise ValueError("changed_count does not match observations")
        if any(item.changed and not item.processed for item in self.results):
            raise ValueError("an unprocessed target cannot change")
        return self


def normalize_text(request: NormalizationRequest) -> NormalizationResult:
    """Return observations without mutating the request or deciding obligation state."""
    results = []
    for target in request.targets:
        processed = bool(target.text)
        text = norm_fix(target.text) if target.text else target.text
        results.append(
            NormalizationObservation(
                id=target.id,
                text=text,
                input_fingerprint=target.fingerprint(),
                processed=processed,
                changed=text != target.text,
            )
        )
    return NormalizationResult(
        results=results,
        processed_count=sum(item.processed for item in results),
        changed_count=sum(item.changed for item in results),
    )
