"""Fingerprinting and execution cache key determinism."""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest

from acessilia_toolbox.core.cache import compute_cache_key
from acessilia_toolbox.core.fingerprint import (
    fingerprint_bytes,
    fingerprint_parameters,
    fingerprint_stream,
    is_fingerprint,
)
from acessilia_toolbox.core.provenance import ExecutionProvenance

BASE = {
    "capability_id": "document.structure.extract",
    "capability_version": 1,
    "provider_id": "docling",
    "provider_version": "1.32",
    "input_fingerprints": [fingerprint_bytes(b"document")],
}


def test_fingerprint_is_prefixed_and_well_formed() -> None:
    digest = fingerprint_bytes(b"document")
    assert digest.startswith("sha256:")
    assert is_fingerprint(digest)
    assert not is_fingerprint("sha256:zzz")
    assert not is_fingerprint("deadbeef")


def test_stream_and_buffer_fingerprints_agree() -> None:
    payload = b"a" * (2 * 1024 * 1024 + 7)
    assert fingerprint_stream(io.BytesIO(payload)) == fingerprint_bytes(payload)


def test_parameter_fingerprint_ignores_key_order() -> None:
    assert fingerprint_parameters({"a": 1, "b": 2}) == fingerprint_parameters({"b": 2, "a": 1})
    assert fingerprint_parameters(None) == fingerprint_parameters({})
    assert fingerprint_parameters({"a": 1}) != fingerprint_parameters({"a": 2})


def test_cache_key_is_stable_across_calls() -> None:
    assert compute_cache_key(**BASE) == compute_cache_key(**BASE)


def test_cache_key_ignores_parameter_ordering() -> None:
    first = compute_cache_key(**BASE, parameters={"tables": True, "images": False})
    second = compute_cache_key(**BASE, parameters={"images": False, "tables": True})
    assert first == second


@pytest.mark.parametrize(
    "override",
    [
        {"capability_version": 2},
        {"provider_id": "mineru"},
        {"provider_version": "2.0"},
        {"input_fingerprints": [fingerprint_bytes(b"other")]},
    ],
)
def test_cache_key_changes_when_execution_context_changes(override: dict[str, object]) -> None:
    """A provider upgrade must not silently reuse an incompatible result."""
    assert compute_cache_key(**{**BASE, **override}) != compute_cache_key(**BASE)


def test_cache_key_changes_with_model_versions() -> None:
    with_model = compute_cache_key(**BASE, model_versions={"layout": "1.0"})
    assert with_model != compute_cache_key(**BASE)
    assert with_model != compute_cache_key(**BASE, model_versions={"layout": "1.1"})


def test_cache_key_respects_input_order() -> None:
    a, b = fingerprint_bytes(b"a"), fingerprint_bytes(b"b")
    context = {k: v for k, v in BASE.items() if k != "input_fingerprints"}
    assert compute_cache_key(**context, input_fingerprints=[a, b]) != compute_cache_key(
        **context, input_fingerprints=[b, a]
    )


def test_execution_provenance_serializes_for_transport() -> None:
    started = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    provenance = ExecutionProvenance(
        capability="document.structure.extract",
        capability_version=1,
        provider="docling",
        provider_version="1.32",
        input_fingerprints=[fingerprint_bytes(b"document")],
        parameters_hash=fingerprint_parameters({}),
        started_at=started,
        completed_at=started,
        duration_ms=4521,
    )
    payload = provenance.to_payload()

    assert payload["provider"] == "docling"
    assert payload["cache_hit"] is False
    assert payload["duration_ms"] == 4521


def test_execution_provenance_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        ExecutionProvenance.model_validate({"backend": "docling"})
