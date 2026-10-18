"""HTTP adapter for MinerU 4's self-hosted V1 parsing API."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter, sleep
from typing import Any
from urllib.parse import quote

import httpx

from acessilia_toolbox.core.errors import (
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth
from acessilia_toolbox.providers.mineru_document import MineruDocument

PARSE_PATH = "/v1/file_parse"
HEALTH_PATH = "/v1/health"


class MineruProvider:
    """Call MinerU's isolated API; normalize its MiddleJson into the shared contract."""

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
        clock = perf_counter()
        with self._client() as client:
            document = self._parse(client, payload, filename, media_type, parameters)
            producer = document.get("metadata", {}).get("producer", {})
            version = (
                producer.get("version") if isinstance(producer, dict) else None
            ) or self.descriptor.version

        return ExtractionResult(
            document=MineruDocument(document),
            backend="mineru",
            started_at=started_at,
            completed_at=datetime.now(UTC),
            duration_ms=round((perf_counter() - clock) * 1000),
            version=str(version),
            configuration={
                "extractor": "mineru-v1",
                "base_url": self.base_url,
                "capability": capability_id,
                "component_versions": {"mineru": str(version)},
                **dict(parameters or {}),
            },
        )

    def versions(self) -> dict[str, str]:
        with self._client(timeout=10.0) as client:
            version = self._server_version(client)
        return {"provider": version or self.descriptor.version}

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            with self._client(timeout=10.0) as client:
                response = client.get(HEALTH_PATH)
                response.raise_for_status()
                version = self._server_version(client, response=response)
            return ProviderHealth(
                provider=self.descriptor.id,
                healthy=True,
                version=version or self.descriptor.version,
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
        options = {**self.config, **dict(parameters or {})}
        query: dict[str, Any] = {
            "tier": str(options.get("tier", "basic")),
            "ocr_mode": str(options.get("ocr_mode", "auto")),
            "output_formats": "middle_json",
            "response_format": "json",
            "wait_timeout": min(3600, max(1, int(self.descriptor.timeout_seconds))),
        }
        if "page_range" in options:
            query["page_range"] = str(options["page_range"])
        elif "start_page_id" in options or "end_page_id" in options:
            start = int(options.get("start_page_id", 0)) + 1
            end = int(options.get("end_page_id", 99999)) + 1
            query["page_range"] = f"{start}-{end}" if end < 99999 else f"{start}-r1"

        try:
            response = client.post(
                PARSE_PATH,
                params=query,
                files={"files": (filename, payload, media_type)},
            )
            deadline = perf_counter() + self.descriptor.timeout_seconds
            while response.status_code == 202:
                task_id = response.json().get("task_id")
                if not isinstance(task_id, str) or not task_id:
                    raise ProviderExecutionError(
                        "MinerU returned 202 without a task ID",
                        provider=self.descriptor.id,
                    )
                if perf_counter() >= deadline:
                    raise ProviderTimeoutError(
                        "MinerU parse job exceeded the timeout",
                        provider=self.descriptor.id,
                    )
                sleep(min(1, max(0, deadline - perf_counter())))
                response = client.get(
                    f"/v1/tasks/{quote(task_id, safe='')}/result",
                    params={"response_format": "json"},
                )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"MinerU timed out after {self.descriptor.timeout_seconds}s",
                provider=self.descriptor.id,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderExecutionError(
                f"MinerU rejected the document: HTTP {exc.response.status_code}",
                provider=self.descriptor.id,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"MinerU is unreachable at {self.base_url}",
                provider=self.descriptor.id,
            ) from exc

        try:
            result = response.json()
        except ValueError as exc:
            raise ProviderExecutionError(
                "MinerU returned invalid JSON", provider=self.descriptor.id
            ) from exc
        files = result.get("files") if isinstance(result, dict) else None
        if (
            not isinstance(result, dict)
            or result.get("status") != "completed"
            or not isinstance(files, list)
            or len(files) != 1
        ):
            raise ProviderExecutionError(
                "MinerU returned an incomplete or malformed parsing result",
                provider=self.descriptor.id,
            )
        file_result = files[0]
        if not isinstance(file_result, dict) or file_result.get("status") != "completed":
            raise ProviderExecutionError(
                "MinerU failed to parse the document",
                provider=self.descriptor.id,
            )
        artifacts = file_result.get("content") or {}
        middle_json = artifacts.get("middle_json") if isinstance(artifacts, dict) else None

        if middle_json is None:
            output = file_result.get("output_files") or {}
            artifact = output.get("middle_json") or {}
            file_id = artifact.get("file_id") if isinstance(artifact, dict) else None
            if not isinstance(file_id, str) or not file_id:
                raise ProviderExecutionError(
                    "MinerU did not return a middle_json artifact",
                    provider=self.descriptor.id,
                )
            try:
                download = client.get(
                    f"/v1/files/{quote(file_id, safe='')}/content",
                    follow_redirects=True,
                )
                download.raise_for_status()
                middle_json = download.json()
            except httpx.HTTPError as exc:
                raise ProviderExecutionError(
                    "MinerU middle_json artifact could not be downloaded",
                    provider=self.descriptor.id,
                ) from exc

        if isinstance(middle_json, str):
            try:
                middle_json = json.loads(middle_json)
            except json.JSONDecodeError as exc:
                raise ProviderExecutionError(
                    "MinerU returned invalid middle_json",
                    provider=self.descriptor.id,
                ) from exc
        if (
            not isinstance(middle_json, dict)
            or middle_json.get("schema") != "docvortex.middle"
            or not isinstance(middle_json.get("pages"), list)
        ):
            raise ProviderExecutionError(
                "MinerU returned an unsupported middle_json schema",
                provider=self.descriptor.id,
            )
        return middle_json

    def _server_version(
        self, client: httpx.Client, *, response: httpx.Response | None = None
    ) -> str:
        try:
            response = response or client.get(HEALTH_PATH)
            response.raise_for_status()
            info = response.json()
            return str(info.get("version") or "") if isinstance(info, dict) else ""
        except (httpx.HTTPError, ValueError):
            return ""


__all__ = ["MineruProvider"]
