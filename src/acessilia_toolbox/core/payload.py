"""Decoding helpers for capability input payloads.

A capability declares the media types it accepts, and the executor enforces
them. Providers must therefore read the payload according to that declaration:
a capability that accepts ``application/json`` receives a JSON document, while
one that accepts ``text/plain`` receives the raw text.

Reading a JSON document as raw text silently produces nonsense — the braces and
quotes become part of the value — so the decoding lives here, next to the
contract it implements, instead of being repeated per provider.
"""

from __future__ import annotations

import json
from typing import Any

from acessilia_toolbox.core.errors import InvalidInputError

JSON_MEDIA_TYPE = "application/json"


def decode_text(payload: bytes) -> str:
    """Return the payload as text, tolerating undecodable bytes."""
    return payload.decode("utf-8", errors="replace").strip()


def decode_json_object(payload: bytes, *, capability: str | None = None) -> dict[str, Any]:
    """Parse a JSON object payload, rejecting anything that is not an object."""
    try:
        parsed = json.loads(payload.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise InvalidInputError(
            f"payload is not valid JSON: {exc}", capability=capability
        ) from exc
    if not isinstance(parsed, dict):
        raise InvalidInputError(
            f"payload must be a JSON object, got {type(parsed).__name__}",
            capability=capability,
        )
    return parsed


def latex_from_payload(
    payload: bytes, *, media_type: str, capability: str | None = None
) -> str:
    """Extract the LaTeX expression from a payload.

    ``artifact/latex@1`` is a JSON object carrying the expression under
    ``latex``, so a JSON payload is parsed and the field read. ``text/plain``
    carries the expression itself, which keeps the capability usable for a
    caller that has nothing but the raw string.
    """
    if media_type == JSON_MEDIA_TYPE:
        document = decode_json_object(payload, capability=capability)
        latex = document.get("latex")
        if not isinstance(latex, str) or not latex.strip():
            raise InvalidInputError(
                "JSON payload must carry a non-empty 'latex' string",
                capability=capability,
            )
        return latex.strip()
    return decode_text(payload)
