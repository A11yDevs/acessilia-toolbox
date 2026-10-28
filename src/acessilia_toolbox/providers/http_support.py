"""HTTP client helper utilities for provider adapters."""

from __future__ import annotations

import httpx


def extract_error_detail(response: httpx.Response, max_length: int = 500) -> str:
    """Extract a sanitized, bounded error detail message from an HTTP response.

    Handles FastAPI/Pydantic-style JSON responses (such as ``{"detail": ...}``)
    as well as plain text error bodies, normalizing whitespace and capping
    length to prevent unbound memory or terminal spam.
    """
    try:
        data = response.json()
        if isinstance(data, dict):
            if "detail" in data:
                detail = data["detail"]
                if isinstance(detail, list):
                    # Pydantic validation errors list
                    items = [
                        f"{'.'.join(str(loc) for loc in err.get('loc', []))}: {err.get('msg', '')}"
                        if isinstance(err, dict)
                        else str(err)
                        for err in detail
                    ]
                    return " ".join(" ".join(items).split())[:max_length]
                return " ".join(str(detail).split())[:max_length]
            if "message" in data:
                return " ".join(str(data["message"]).split())[:max_length]
            if "error" in data:
                return " ".join(str(data["error"]).split())[:max_length]
    except Exception:
        pass

    text = response.text or ""
    cleaned = " ".join(text.split())
    return cleaned[:max_length]


def format_http_error(
    backend: str,
    response: httpx.Response,
    action: str = "rejected",
    max_length: int = 500,
) -> str:
    """Format an error message preserving the response status and sanitized detail."""
    detail = extract_error_detail(response, max_length=max_length)
    base = f"{backend} {action}: HTTP {response.status_code}"
    if detail:
        return f"{base} - {detail}"
    return base
