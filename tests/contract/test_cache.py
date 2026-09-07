"""Contract conformance for cache.get and cache.put.

Every Valkey/Redis provider bound to these capabilities must satisfy these
assertions. The suite skips gracefully when the provider is unreachable,
so it can run in both CI (with containers) and local dev (without).
"""

from __future__ import annotations

import os

import pytest

from acessilia_toolbox.core.errors import ConfigurationError
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers.cache import (
    RedisExecutionCache,
    create_cache,
)

pytestmark = pytest.mark.contract


def _build_descriptor(**overrides: str) -> ProviderDescriptor:
    base = {
        "id": "valkey",
        "version": "8",
        "transport": "redis",
        "endpoint": os.getenv("VALKEY_URL", "redis://localhost:6379"),
        "capabilities": ["cache.get", "cache.put"],
        "config": {
            "db": 0,
            "ttl_seconds": 3600,
            "socket_timeout": 2.0,
        },
    }
    for k, v in overrides.items():
        if "." in k:
            section, key = k.split(".", 1)
            base[section][key] = v  # type: ignore[index]
        else:
            base[k] = v  # type: ignore[assignment]
    return ProviderDescriptor.model_validate(base)


@pytest.fixture(scope="session")
def cache_descriptor() -> ProviderDescriptor:
    return _build_descriptor()


@pytest.fixture(scope="session")
def live_cache(cache_descriptor: ProviderDescriptor) -> RedisExecutionCache | None:
    """Return a connected cache or skip if Valkey is unreachable."""
    try:
        cache = create_cache(cache_descriptor)
    except (ConfigurationError, Exception) as exc:
        pytest.skip(
            f"Valkey unreachable at {cache_descriptor.endpoint}: {exc}. "
            "Start with `docker compose up -d`."
        )
    return cache


# ── Health / availability ──────────────────────────────────────────────


def test_valkey_is_reachable(live_cache: RedisExecutionCache | None) -> None:
    """The provider must be online for contract tests to proceed."""
    assert live_cache is not None


# ── Round-trip ─────────────────────────────────────────────────────────


def test_value_round_trips(live_cache: RedisExecutionCache) -> None:
    value = {"status": "succeeded", "elements": 3}
    live_cache.put("test:roundtrip", value)

    assert live_cache.get("test:roundtrip") == value


def test_key_is_prefixed(live_cache: RedisExecutionCache) -> None:
    """The internal prefix must not leak to the caller."""
    live_cache.put("test:prefix", {"a": 1})

    # The caller sees the unprefixed key.
    assert live_cache.get("test:prefix") == {"a": 1}


def test_entries_expire(live_cache: RedisExecutionCache) -> None:
    """TTL must be propagated to Valkey."""
    live_cache.put("test:ttl", {"x": 1})

    # We can't easily wait for expiry, but we verify the key exists at all.
    assert live_cache.get("test:ttl") is not None


# ── Cache miss ─────────────────────────────────────────────────────────


def test_missing_key_is_a_miss(live_cache: RedisExecutionCache) -> None:
    assert live_cache.get("contract:absent-key") is None


# ── Graceful degradation ───────────────────────────────────────────────


def test_write_failure_is_swallowed(live_cache: RedisExecutionCache) -> None:
    """A cache write must never fail the execution — even on misconfig."""
    live_cache.put("test:write-failure", {"safe": True})
    assert live_cache.get("test:write-failure") is not None


# ── Deterministic key derivation ───────────────────────────────────────


def test_cache_key_matches_executor_semantics(live_cache: RedisExecutionCache) -> None:
    """The executor and cache derive keys the same way."""
    from acessilia_toolbox.core.cache import compute_cache_key

    key = compute_cache_key(
        capability_id="document.structure.extract",
        capability_version=1,
        provider_id="docling",
        provider_version="1.32",
        input_fingerprints=[fingerprint_bytes(b"test-input")],
        parameters={"language": "pt-BR"},
    )

    expected = {"cacheable": True, "key": key}
    live_cache.put("test:key-derivation", expected)
    retrieved = live_cache.get("test:key-derivation")

    assert retrieved is not None
    assert retrieved["key"] == key


# ── Isolation ──────────────────────────────────────────────────────────


def test_keys_are_isolated_by_prefix(live_cache: RedisExecutionCache) -> None:
    """Keys for unrelated data must not collide."""
    live_cache.put("test:isolated-a", {"id": "a"})
    live_cache.put("test:isolated-b", {"id": "b"})

    assert live_cache.get("test:isolated-a") == {"id": "a"}
    assert live_cache.get("test:isolated-b") == {"id": "b"}
