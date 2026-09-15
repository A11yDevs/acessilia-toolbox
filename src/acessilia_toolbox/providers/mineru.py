"""mineru-api provider adapter.

Follows the same thin-adapter contract as the Docling provider: HTTP
transport plus shape mapping only.  MinerU runs in its own container (or as
a local ``mineru-api`` process), so the toolbox image carries no ML runtime.

Endpoint contract (mineru 2.x, ``POST /file_parse``):
    multipart/form-data with fields:
        files             — the document (PDF or image)
        backend           — "pipeline" (CPU) or "vlm-*" (GPU)
        lang_list         — OCR language hints, e.g. ["pt"]
        return_md         — markdown text
        return_middle_json— structured tree (the payload we normalize)
        return_content_list— flat ordered content list
        start_page_id / end_page_id — page range selection

Response: {"backend", "version", "results": {<name>: {md_content?,
middle_json?, content_list?, ...}}}
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth
from acessilia_toolbox.providers.mineru_document import MineruDocument

PARSE_PATH = "/file_parse"

VERSION_KEYS = ("version", "mineru_version")
COMPONENT_KEYS = ("version", "backend")


class MineruProvider:
    """Calls mineru-api over REST and returns a raw extraction."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self.base_url = (descriptor.endpoint or "").rstrip("/")
        self.config: dict[str, Any] = dict(descriptor.config)

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        started_at = datetime.now(UTC)
        started_clock = perf_counter()

        with self._client() as client:
            document = self._parse(
                client, payload, filename, media_type, parameters
            )
            server_version = self._server_version(client)

        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document=MineruDocument(document),
            backend="mineru",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=round((perf_counter() - started_clock) * 1000),
            version=server_version or self.descriptor.version,
            configuration={
                "extractor": "mineru-api",
                "base_url": self.base_url,
                "capability": capability_id,
                "component_versions": (
                    {"mineru": server_version} if server_version else {}
                ),
                **dict(parameters or {}),
            },
        )

    def versions(self) -> dict[str, str]:
        with self._client(timeout=10.0) as client:
            reported = self._server_version(client)
        return {"provider": reported or self.descriptor.version}

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            with self._client(timeout=10.0) as client:
                # mineru-api is a FastAPI app; /docs is always present and
                # cheaper than parsing, so we use the OpenAPI document as a
                # liveness check (no dedicated /health endpoint exists).
                response = client.get("/openapi.json")
                response.raise_for_status()
                return ProviderHealth(
                    provider=self.descriptor.id,
                    healthy=True,
                    version=(
                        self._server_version(client) or self.descriptor.version
                    ),
                    checked_at=checked_at,
                )
        except Exception as exc:
            return ProviderHealth(
                provider=self.descriptor.id,
                healthy=False,
                detail=f"{type(exc).__name__}: {exc}",
                checked_at=checked_at,
            )

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=timeout or self.descriptor.timeout_seconds,
        )

    def _parse(
        self,
        client: httpx.Client,
        payload: bytes,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        params = {**self.config, **dict(parameters or {})}
        # MinerU's pipeline backend accepts a fixed language set (ch,
        # ch_server, korean, ta, te, ka, th, el, arabic, east_slavic,
        # cyrillic, devanagari)
        # and rejects BCP-47 codes like "pt". Latin scripts fall back to "ch",
        # which handles them out of the box.
        lang = str(params.get("lang", "ch"))
        backend = str(params.get("backend", "pipeline"))
        start_page = int(params.get("start_page_id", 0))
        end_page = int(params.get("end_page_id", 99999))

        data: dict[str, Any] = {
            "backend": backend,
            "lang_list": lang,
            "return_md": "true",
            "return_middle_json": "true",
            "return_content_list": "true",
            "start_page_id": str(start_page),
            "end_page_id": str(end_page),
            "parse_method": str(params.get("parse_method", "auto")),
            "formula_enable": str(
                bool(params.get("formula_enable", True))
            ).lower(),
            "table_enable": str(
                bool(params.get("table_enable", True))
            ).lower(),
        }
        try:
            response = client.post(
                PARSE_PATH,
                files={"files": (filename, payload, media_type)},
                data=data,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                "mineru-api timed out after "
                f"{self.descriptor.timeout_seconds}s",
                provider=self.descriptor.id,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderExecutionError(
                "mineru-api rejected the document: "
                f"HTTP {exc.response.status_code}",
                provider=self.descriptor.id,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"mineru-api is unreachable at {self.base_url}",
                provider=self.descriptor.id,
            ) from exc

        result = response.json()
        if not isinstance(result, dict):
            raise ProviderExecutionError(
                "mineru-api returned an unexpected payload",
                provider=self.descriptor.id,
            )

        results = result.get("results")
        if not isinstance(results, dict) or not results:
            raise ProviderExecutionError(
                "mineru-api returned no results",
                provider=self.descriptor.id,
            )

        # results maps the uploaded file's stem to its artifacts. Take the
        # first (and normally only) entry.
        first = next(iter(results.values()))
        if not isinstance(first, dict):
            raise ProviderExecutionError(
                "mineru-api returned malformed results",
                provider=self.descriptor.id,
            )

        middle_json = first.get("middle_json")
        # mineru-api serializes middle_json as a JSON string in some builds
        # and as a nested object in others; accept both.
        if isinstance(middle_json, str):
            try:
                middle_json = json.loads(middle_json)
            except json.JSONDecodeError as exc:
                raise ProviderExecutionError(
                    "mineru-api returned malformed structured content "
                    "(middle_json is a non-JSON string)",
                    provider=self.descriptor.id,
                ) from exc
        if not isinstance(middle_json, dict) or not middle_json:
            raise ProviderExecutionError(
                "mineru-api returned no structured content (middle_json)",
                provider=self.descriptor.id,
            )
        # Carry the markdown along for downstream consumers that want the
        # rendered text without re-deriving it from the tree.
        if first.get("md_content"):
            middle_json["md_content"] = first["md_content"]
        if first.get("content_list"):
            middle_json["content_list"] = first["content_list"]
        middle_json.setdefault("_backend", result.get("backend", backend))
        return middle_json

    def _server_version(self, client: httpx.Client) -> str:
        # mineru-api has no version endpoint; the parse response carries the
        # version, and the OpenAPI document is our best cheap probe.
        try:
            response = client.get("/openapi.json")
            response.raise_for_status()
            info = response.json().get("info", {})
            if isinstance(info, dict):
                return str(info.get("version", ""))
            return ""
        except Exception:
            return ""


__all__ = ["MineruProvider"]
