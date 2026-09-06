"""Redis/Valkey cache adapter.

Caching is an optimization: when the cache misbehaves the execution must still
succeed, only without the speedup.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from acessilia_toolbox.core.errors import ConfigurationError
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers.cache import (
    KEY_PREFIX,
    RedisExecutionCache,
    create_cache,
)


class FakeRedis:
    def __init__(self, *, failing: bool = False) -> None:
        self.entries: dict[str, str] = {}
        self.failing = failing
        self.expirations: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        if self.failing:
            raise ConnectionError("valkey unreachable")
        return self.entries.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        if self.failing:
            raise ConnectionError("valkey unreachable")
        self.entries[key] = value
        self.expirations[key] = ttl


def descriptor(**overrides: Any) -> ProviderDescriptor:
    base = {
        "id": "valkey",
        "transport": "redis",
        "endpoint": "redis://localhost:6379",
        "capabilities": ["cache.get", "cache.put"],
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def cache_with(client: FakeRedis, **overrides: Any) -> RedisExecutionCache:
    cache = RedisExecutionCache.__new__(RedisExecutionCache)
    cache._client = client  # type: ignore[attr-defined]
    cache.ttl = int(overrides.get("ttl", 3600))
    return cache


def test_value_round_trips() -> None:
    client = FakeRedis()
    cache = cache_with(client)

    cache.put("key", {"status": "succeeded"})

    assert cache.get("key") == {"status": "succeeded"}
    assert KEY_PREFIX + "key" in client.entries


def test_entries_expire() -> None:
    client = FakeRedis()
    cache_with(client, ttl=60).put("key", {"a": 1})

    assert client.expirations[KEY_PREFIX + "key"] == 60


def test_missing_key_is_a_miss() -> None:
    assert cache_with(FakeRedis()).get("absent") is None


def test_unreachable_cache_degrades_to_a_miss() -> None:
    """An outage must not fail the execution."""
    assert cache_with(FakeRedis(failing=True)).get("key") is None


def test_write_failure_is_swallowed() -> None:
    cache_with(FakeRedis(failing=True)).put("key", {"a": 1})


def test_corrupt_entry_is_treated_as_a_miss() -> None:
    client = FakeRedis()
    client.entries[KEY_PREFIX + "key"] = "{not json"

    assert cache_with(client).get("key") is None


def test_non_object_entry_is_rejected() -> None:
    client = FakeRedis()
    client.entries[KEY_PREFIX + "key"] = json.dumps([1, 2, 3])

    assert cache_with(client).get("key") is None


def test_factory_requires_the_redis_transport() -> None:
    with pytest.raises(ConfigurationError):
        create_cache(descriptor(transport="http"))
