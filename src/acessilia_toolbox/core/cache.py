"""Deterministic execution cache keys.

The key binds a result to everything that could change it. Upgrading a
provider or a model therefore yields a new key instead of silently reusing
an incompatible cached result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from acessilia_toolbox.core.fingerprint import canonical_json, fingerprint_bytes


def compute_cache_key(
    *,
    capability_id: str,
    capability_version: int,
    provider_id: str,
    provider_version: str,
    input_fingerprints: Sequence[str],
    parameters: Mapping[str, object] | None = None,
    model_versions: Mapping[str, str] | None = None,
) -> str:
    """Derive a stable key from the full execution context.

    Input order is preserved: for capabilities taking several inputs the
    ordering is part of the request's meaning.
    """
    material = canonical_json(
        {
            "capability": f"{capability_id}@{capability_version}",
            "provider": f"{provider_id}@{provider_version}",
            "inputs": list(input_fingerprints),
            "parameters": dict(parameters or {}),
            "models": dict(model_versions or {}),
        }
    )
    return fingerprint_bytes(material.encode("utf-8"))
