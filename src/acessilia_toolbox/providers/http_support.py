"""HTTP client helper utilities for provider adapters."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import httpx

# Keys sidecars use for the human-readable reason a request failed. FastAPI
# (and therefore docling-serve, mineru-api and most of our sidecars) answers
# with ``detail``; other services use ``message`` or ``error``.
DETAIL_KEYS = ("detail", "message", "error")

DEFAULT_MAX_LENGTH = 500

# ANSI escape sequences (CSI and two-character escapes). They must not reach a
# log line or an API consumer: a sidecar can emit colored output or binary junk.
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])")


def _sanitize(value: Any, max_length: int) -> str:
    """Collapse whitespace and drop control characters from ``value``.

    The result is logged and returned to clients, so ANSI escapes, NUL bytes
    and other control/format characters (including the replacement characters
    of a binary body) must not survive.
    """
    text = value if isinstance(value, str) else str(value)
    text = _ANSI_ESCAPE.sub("", text)
    # Bound the work before the per-character pass: a proxy can answer with a
    # large HTML error page, and only the first few hundred characters can ever
    # reach the message. The multiplier leaves room for controls and whitespace
    # that collapse away below.
    text = text[: max_length * 8]
    cleaned = "".join(
        " " if char == "\ufffd" or unicodedata.category(char).startswith("C") else char
        for char in text
    )
    return " ".join(cleaned.split())[:max_length]


def _detail_from_list(items: list[Any], max_length: int) -> str:
    """Render a Pydantic validation-error list into ``location: message`` pairs."""
    parts: list[str] = []
    for item in items:
        if isinstance(item, dict) and "loc" in item:
            location = ".".join(str(part) for part in item.get("loc") or [])
            parts.append(f"{location}: {item.get('msg', '')}")
        elif isinstance(item, dict) and "msg" in item:
            parts.append(str(item["msg"]))
        else:
            parts.append(str(item))
    return _sanitize(" ".join(parts), max_length)


def _detail_from_value(value: Any, max_length: int) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return _detail_from_list(value, max_length)
    if isinstance(value, dict):
        # Nested error object, e.g. {"error": {"message": "..."}}.
        for key in ("message", "detail", "msg"):
            nested = value.get(key)
            if nested:
                return _detail_from_value(nested, max_length)
        return ""
    return _sanitize(value, max_length)


def extract_error_detail(
    response: httpx.Response, max_length: int = DEFAULT_MAX_LENGTH
) -> str:
    """Extract a sanitized, bounded error detail message from an HTTP response.

    Handles FastAPI/Pydantic-style JSON responses (such as ``{"detail": ...}``,
    including the validation-error list) as well as plain text error bodies,
    normalizing whitespace, stripping control characters and capping the length
    to prevent unbounded memory use or terminal spam.
    """
    try:
        data = response.json()
    except ValueError:
        return _sanitize(response.text, max_length)

    if isinstance(data, dict):
        for key in DETAIL_KEYS:
            if key in data:
                # The sidecar answered with its own error schema. If the value
                # is unusable (null, empty list), the status code is all we
                # have; do not echo the raw JSON back as "None".
                return _detail_from_value(data[key], max_length)
        # No known key: the raw body still carries the sidecar's wording.
        return _sanitize(response.text, max_length)
    if isinstance(data, list):
        return _detail_from_list(data, max_length)
    if isinstance(data, str):
        return _sanitize(data, max_length)
    return _sanitize(response.text, max_length)


def format_http_error(
    backend: str,
    response: httpx.Response,
    action: str = "rejected",
    max_length: int = DEFAULT_MAX_LENGTH,
) -> str:
    """Format an error message preserving the response status and sanitized detail."""
    detail = extract_error_detail(response, max_length=max_length)
    base = f"{backend} {action}: HTTP {response.status_code}"
    if detail:
        return f"{base} - {detail}"
    return base
