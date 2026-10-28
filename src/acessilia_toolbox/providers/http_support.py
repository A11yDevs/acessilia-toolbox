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
            for key in ("detail", "message", "error"):
                if key in data:
                    val = data[key]
                    if isinstance(val, list):
                        # Pydantic validation errors list or list of error items
                        items = []
                        for err in val:
                            if isinstance(err, dict) and "loc" in err:
                                loc_str = ".".join(str(loc) for loc in err.get("loc", []))
                                items.append(f"{loc_str}: {err.get('msg', '')}")
                            else:
                                items.append(str(err))
                        return " ".join(" ".join(items).split())[:max_length]
                    if isinstance(val, dict):
                        # Nested error object (e.g. {"error": {"message": "..."}})
                        if "message" in val:
                            return " ".join(str(val["message"]).split())[:max_length]
                        if "detail" in val:
                            return " ".join(str(val["detail"]).split())[:max_length]
                    return " ".join(str(val).split())[:max_length]
        elif isinstance(data, list):
            items = [str(item) for item in data]
            return " ".join(" ".join(items).split())[:max_length]
        elif isinstance(data, str):
            return " ".join(data.split())[:max_length]
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
