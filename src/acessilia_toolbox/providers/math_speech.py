"""math-speech provider adapter for ``math.convert`` and ``math.verbalize``.

Thin client of the out-of-process ``math-speech`` sidecar
(docker/math-speech.Dockerfile): MathJax converts LaTeX to MathML and MathCAT
speaks it in Brazilian Portuguese. ``math.verbalize@1`` is pt-BR only
(ADR-0007). The reverse direction of ``math.convert`` (``mathml-to-latex``)
has no MathJax counterpart and stays a local pure-Python mapper.

Errors follow the sidecar's codes: input it rejects (unparseable LaTeX or
MathML, a bad parameter, an oversized expression) is the caller's
(``InvalidInputError``, 400); an unreachable or starting sidecar is
``ProviderUnavailableError`` (503); anything else, including MathCAT failing
on valid MathML or MathML the local mapper cannot convert, is
``ProviderExecutionError``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx

from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderExecutionError,
    ProviderNotBoundError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.payload import latex_from_payload
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth
from acessilia_toolbox.providers.mathml_latex import mathml_to_latex

LOG = logging.getLogger(__name__)

CONVERT_PATH = "/convert"
SPEAK_PATH = "/speak"
VERSION_PATH = "/version"

# Everything the sidecar reports that shapes the output; all of it enters the
# cache key, so a MathJax or MathCAT upgrade never reuses text made by older
# rules.
VERSION_KEYS = ("version", "mathjax", "mathcat")

LANGUAGE = "pt-BR"
DIRECTIONS = ("latex-to-mathml", "mathml-to-latex")
STYLES = ("ClearSpeak", "SimpleSpeak")
VERBOSITIES = ("Terse", "Medium", "Verbose")


class MathSpeechProvider:
    """Calls the math-speech sidecar to convert and speak formulas."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self.base_url = (descriptor.endpoint or "").rstrip("/")

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=timeout or self.descriptor.timeout_seconds,
        )

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        params = dict(parameters or {})
        expression = latex_from_payload(payload, media_type=media_type, capability=capability_id)

        started_at = datetime.now(UTC)
        started_clock = perf_counter()
        if capability_id == "math.convert":
            direction = _choice(params, "direction", DIRECTIONS, "latex-to-mathml")
            configuration: dict[str, Any] = {"direction": direction}
            if direction == "mathml-to-latex":
                try:
                    latex = mathml_to_latex(expression)
                except ValueError as exc:
                    raise ProviderExecutionError(str(exc), provider=self.descriptor.id) from exc
                document = {
                    "latex": latex,
                    "mathml": expression,
                    "direction": direction,
                }
                toolchain: dict[str, str] = {}
            else:
                with self._client() as client:
                    answer = self._post(client, CONVERT_PATH, {"latex": expression})
                    toolchain = self._server_versions(client)
                document = {
                    "latex": expression,
                    "mathml": _field(answer, "mathml", self.descriptor.id),
                    "direction": direction,
                }
        elif capability_id == "math.verbalize":
            style = _choice(params, "style", STYLES, "ClearSpeak")
            verbosity = _choice(params, "verbosity", VERBOSITIES, "Medium")
            configuration = {"style": style, "verbosity": verbosity}
            with self._client() as client:
                body = {
                    "latex": expression,
                    "language": LANGUAGE,
                    "style": style,
                    "verbosity": verbosity,
                }
                answer = self._post(client, SPEAK_PATH, body)
                toolchain = self._server_versions(client)
            document = {
                "latex": expression,
                "verbalized": _field(answer, "speech", self.descriptor.id),
                "language": LANGUAGE,
            }
        else:
            raise ProviderNotBoundError(
                f"provider {self.descriptor.id} does not serve {capability_id}",
                provider=self.descriptor.id,
            )

        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document=document,
            backend=self.descriptor.id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=int((perf_counter() - started_clock) * 1000),
            version=toolchain.get("version") or self.descriptor.version,
            configuration={
                "provider": self.descriptor.id,
                "capability": capability_id,
                **configuration,
            },
        )

    def _post(self, client: httpx.Client, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = client.post(path, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"math-speech timed out after {self.descriptor.timeout_seconds}s",
                provider=self.descriptor.id,
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                f"math-speech is not reachable at {self.base_url}: {exc}",
                provider=self.descriptor.id,
            ) from exc

        if response.status_code in (400, 413):
            # The sidecar's detail names the error code, never the expression.
            raise InvalidInputError(
                f"math-speech rejected the expression: {_detail(response)}",
                provider=self.descriptor.id,
            )
        if response.status_code == 503:
            raise ProviderUnavailableError(
                f"math-speech is not ready: {_detail(response)}",
                provider=self.descriptor.id,
            )
        if response.status_code != 200:
            raise ProviderExecutionError(
                f"math-speech failed with status {response.status_code}: {_detail(response)}",
                provider=self.descriptor.id,
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderExecutionError(
                "math-speech returned a body that is not JSON",
                provider=self.descriptor.id,
            ) from exc
        if not isinstance(data, dict):
            raise ProviderExecutionError(
                "math-speech returned a JSON body that is not an object",
                provider=self.descriptor.id,
            )
        return data

    def versions(self) -> dict[str, str]:
        try:
            with self._client(timeout=5.0) as client:
                reported = self._server_versions(client)
        except Exception:
            # The executor tolerates version failures; the declared version is
            # then the only identity available for the cache key.
            LOG.debug("math-speech version probe failed", exc_info=True)
            reported = {}
        return {"provider": reported.get("version") or self.descriptor.version, **reported}

    def _server_versions(self, client: httpx.Client) -> dict[str, str]:
        """Version fields the sidecar reports, filtered to output identity."""
        try:
            response = client.get(VERSION_PATH)
            data = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: str(v) for k, v in data.items() if k in VERSION_KEYS}

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            with self._client(timeout=10.0) as client:
                response = client.get(self.descriptor.health_path or "/health")
                response.raise_for_status()
                data = response.json()
            version = (data.get("version") if isinstance(data, dict) else None) or (
                self.descriptor.version
            )
            return ProviderHealth(
                provider=self.descriptor.id,
                healthy=True,
                version=version,
                checked_at=checked_at,
            )
        except Exception as exc:
            return ProviderHealth(
                provider=self.descriptor.id,
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
                checked_at=checked_at,
            )


def _choice(params: Mapping[str, Any], name: str, allowed: tuple[str, ...], default: str) -> str:
    value = params.get(name, default)
    if value not in allowed:
        raise InvalidInputError(f"parameter '{name}' must be one of {list(allowed)}, got {value!r}")
    return str(value)


def _field(answer: Mapping[str, Any], name: str, provider: str) -> str:
    value = answer.get(name)
    if not isinstance(value, str):
        raise ProviderExecutionError(f"math-speech response lacks '{name}'", provider=provider)
    return value


def _detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(data, dict) and "detail" in data:
        return str(data["detail"])
    return str(data)[:500]
