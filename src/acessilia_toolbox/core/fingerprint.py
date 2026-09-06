"""Content fingerprinting shared by provenance and cache keys.

Identity is derived from content, never from filenames, so the same bytes
always produce the same identifier regardless of how they were supplied.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import IO, Any

DIGEST_PREFIX = "sha256:"
_CHUNK_SIZE = 1024 * 1024


def fingerprint_bytes(data: bytes) -> str:
    return DIGEST_PREFIX + hashlib.sha256(data).hexdigest()


def fingerprint_stream(stream: IO[bytes]) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(_CHUNK_SIZE):
        digest.update(chunk)
    return DIGEST_PREFIX + digest.hexdigest()


def canonical_json(value: Any) -> str:
    """Serialize deterministically so equivalent inputs hash identically."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint_parameters(parameters: Mapping[str, Any] | None) -> str:
    return fingerprint_bytes(canonical_json(dict(parameters or {})).encode("utf-8"))


def is_fingerprint(value: str) -> bool:
    if not value.startswith(DIGEST_PREFIX):
        return False
    digest = value[len(DIGEST_PREFIX) :]
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)
