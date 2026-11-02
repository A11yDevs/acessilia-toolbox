"""Bound materialization for adapters and stores that require bytes."""

from __future__ import annotations

import os
from typing import BinaryIO

from acessilia_toolbox.core.errors import ConfigurationError, UploadTooLargeError

DEFAULT_MAX_IN_MEMORY_INPUT_BYTES = 64 * 1024 * 1024


def in_memory_limit() -> int:
    raw = os.getenv("TOOLBOX_MAX_IN_MEMORY_INPUT_BYTES", str(DEFAULT_MAX_IN_MEMORY_INPUT_BYTES))
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ConfigurationError(
            "TOOLBOX_MAX_IN_MEMORY_INPUT_BYTES must be a positive integer"
        ) from exc
    if limit <= 0:
        raise ConfigurationError("TOOLBOX_MAX_IN_MEMORY_INPUT_BYTES must be a positive integer")
    return limit


def check_in_memory_size(size: int) -> None:
    limit = in_memory_limit()
    if size > limit:
        raise UploadTooLargeError(
            f"input exceeds the in-memory provider limit of {limit} bytes",
            max_bytes=limit,
        )


def read_in_memory(stream: BinaryIO) -> bytes:
    limit = in_memory_limit()
    stream.seek(0)
    payload = stream.read(limit + 1)
    check_in_memory_size(len(payload))
    return payload
