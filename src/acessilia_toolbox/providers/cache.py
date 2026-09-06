"""Execution cache adapters.

The cache is external and replaceable. A cache miss must never be fatal:
degraded caching is preferable to a failed execution.
"""

from __future__ import annotations

import json
from typing import Any

from acessilia_toolbox.core.errors import ConfigurationError
from acessilia_toolbox.core.provider import ProviderDescriptor

DEFAULT_TTL_SECONDS = 7 * 24 * 3600
KEY_PREFIX = "acessilia:execution:"


class RedisExecutionCache:
    """Valkey/Redis cache. Requires the optional `cache` extra."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        try:
            import redis
        except ImportError as exc:
            raise ConfigurationError(
                "cache requires the 'cache' extra: pip install "
                "'acessilia-toolbox[cache]'",
                provider=descriptor.id,
            ) from exc

        config = descriptor.config
        self.ttl = int(config.get("ttl_seconds", DEFAULT_TTL_SECONDS))
        self._client = redis.Redis.from_url(
            descriptor.endpoint or "redis://localhost:6379",
            db=int(config.get("db", 0)),
            socket_timeout=float(config.get("socket_timeout", 2.0)),
        )

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            raw = self._client.get(KEY_PREFIX + key)
        except Exception:
            return None
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        try:
            self._client.setex(
                KEY_PREFIX + key,
                self.ttl,
                json.dumps(value, ensure_ascii=False),
            )
        except Exception:
            # A cache write failure must not fail the execution.
            return None


def create_cache(descriptor: ProviderDescriptor) -> RedisExecutionCache:
    if descriptor.transport != "redis":
        raise ConfigurationError(
            f"cache provider {descriptor.id} must use the redis transport",
            provider=descriptor.id,
        )
    return RedisExecutionCache(descriptor)
