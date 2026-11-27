"""Thin adapter for the bounded document.text.normalize@1 contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib.metadata import version
from time import perf_counter
from typing import Any

from acessilia_toolbox import __version__ as toolbox_version
from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderExecutionError,
    ProviderTimeoutError,
    UnsupportedMediaTypeError,
    UploadTooLargeError,
)
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth
from acessilia_toolbox.operations.text_normalization import (
    MAX_EXECUTION_SECONDS,
    MAX_PAYLOAD_BYTES,
    NormalizationRequest,
    normalize_text,
)

CAPABILITY = "document.text.normalize"
VERSION = "1.0.0"
DOCSTRUCT_VERSION = version("acessilia-docstruct")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant")


def parse_request(payload: bytes) -> NormalizationRequest:
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise UploadTooLargeError(
            f"normalization input exceeds {MAX_PAYLOAD_BYTES} bytes",
            max_bytes=MAX_PAYLOAD_BYTES,
        )
    try:
        raw = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        return NormalizationRequest.model_validate(raw)
    except (ValueError, RecursionError) as exc:
        raise InvalidInputError("invalid text-normalization request") from exc


class DocstructTextProvider:
    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
        capability_version: int = 1,
    ) -> ExtractionResult:
        if capability_id != CAPABILITY or capability_version != 1:
            raise ProviderExecutionError("unsupported text-normalization capability/version")
        if media_type != "application/json":
            raise UnsupportedMediaTypeError("text normalization requires application/json")
        if parameters:
            raise InvalidInputError("text normalization accepts no execution parameters")
        started_at, started = datetime.now(UTC), perf_counter()
        request = parse_request(payload)
        try:
            result = normalize_text(request)
        except Exception as exc:
            raise ProviderExecutionError(
                "text normalization failed", provider=self.descriptor.id
            ) from exc
        duration = perf_counter() - started
        if duration > min(MAX_EXECUTION_SECONDS, self.descriptor.timeout_seconds):
            raise ProviderTimeoutError("text normalization exceeded its execution budget")
        return ExtractionResult(
            document=result.model_dump(mode="json"),
            backend=self.descriptor.id,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            duration_ms=round(duration * 1000),
            version=VERSION,
            configuration={"capability": CAPABILITY},
        )

    def versions(self) -> dict[str, str]:
        return {
            "provider": VERSION,
            "runtime": toolbox_version,
            "docstruct": DOCSTRUCT_VERSION,
            "norm_fix": DOCSTRUCT_VERSION,
        }

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.descriptor.id,
            healthy=True,
            version=VERSION,
            checked_at=datetime.now(UTC),
        )
