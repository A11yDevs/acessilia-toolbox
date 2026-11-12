"""HTTP adapter for the out-of-process Piper neural TTS sidecar.

Calls the piper-tts sidecar container to synthesize plain text into WAV audio.
Keeps the Toolbox model-free (Principle 4 of the Constitution).
"""

from __future__ import annotations

import io
import logging
import wave
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, ClassVar

import httpx

from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.normalization.extraction import BinaryOutput, ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth
from acessilia_toolbox.providers.http_support import format_http_error

LOG = logging.getLogger(__name__)

SYNTHESIZE_PATH = "/synthesize"
HEALTH_PATH = "/health"
VERSION_PATH = "/version"

DEFAULT_PIPER_VOICE = "pt_BR-faber-medium"
DEFAULT_PIPER_LANGUAGE = "pt-BR"


def _compute_wav_duration(payload: bytes) -> float | None:
    """Read the duration in seconds from standard WAV PCM header."""
    try:
        with wave.open(io.BytesIO(payload), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                return round(frames / float(rate), 3)
    except Exception:
        pass
    return None


class PiperTtsProvider:
    """HTTP client adapter for the out-of-process Piper TTS sidecar."""

    supports_streaming_input = True
    backend: ClassVar[str] = "piper-tts"

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self.base_url = (descriptor.endpoint or "").rstrip("/")
        self._version_cache: dict[str, str] | None = None

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
        if capability_id != "audio.synthesize":
            raise ValueError(f"Unsupported capability: {capability_id}")

        started_at = datetime.now(UTC)
        started_clock = perf_counter()

        params = dict(parameters or {})
        config = self.descriptor.config or {}
        voice = str(params.get("voice") or config.get("default_voice") or DEFAULT_PIPER_VOICE)
        language = str(
            params.get("language")
            or config.get("default_language")
            or DEFAULT_PIPER_LANGUAGE
        )

        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidInputError(
                f"payload is not valid UTF-8 text: {exc}", capability=capability_id
            ) from exc

        clean_text = text.strip()
        if not clean_text:
            raise InvalidInputError(
                "text payload contains no content to synthesize", capability=capability_id
            )

        form_data: dict[str, Any] = {
            "text": clean_text,
            "voice": voice,
        }
        for opt_key in ("speaker_id", "length_scale", "noise_scale", "noise_w"):
            if opt_key in params and params[opt_key] is not None:
                form_data[opt_key] = str(params[opt_key])

        with self._client() as client:
            wav_bytes = self._synthesize(client, form_data)
            toolchain = self._server_versions(client)

        completed_at = datetime.now(UTC)
        duration_ms = round((perf_counter() - started_clock) * 1000)
        duration_seconds = _compute_wav_duration(wav_bytes)

        document: dict[str, Any] = {
            "artifact_id": fingerprint_bytes(wav_bytes),
            "media_type": "audio/wav",
            "size_bytes": len(wav_bytes),
            "duration_seconds": duration_seconds,
            "voice": voice,
            "language": language,
            "toolchain": {
                "provider": self.descriptor.id,
                **toolchain,
            },
        }

        stem = Path(filename).stem or "audio"
        binary_output = BinaryOutput(
            payload=wav_bytes,
            media_type="audio/wav",
            filename=f"{stem}.wav",
        )

        return ExtractionResult(
            document=document,
            backend=self.descriptor.id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            version=toolchain.get("piper") or self.descriptor.version,
            configuration={
                "capability": capability_id,
                "voice": voice,
                "language": language,
                **params,
            },
            outputs=(binary_output,),
        )

    def _synthesize(self, client: httpx.Client, data: dict[str, Any]) -> bytes:
        try:
            response = client.post(SYNTHESIZE_PATH, data=data)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"piper-tts synthesis timed out: {exc}", provider=self.descriptor.id
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                f"piper-tts sidecar unreachable: {exc}", provider=self.descriptor.id
            ) from exc

        if response.status_code == 400:
            raise InvalidInputError(
                format_http_error(self.descriptor.id, response, action="rejected input"),
                capability="audio.synthesize",
            )
        if response.status_code == 503:
            raise ProviderUnavailableError(
                format_http_error(self.descriptor.id, response, action="unavailable"),
                provider=self.descriptor.id,
            )
        if not response.is_success:
            raise ProviderExecutionError(
                format_http_error(self.descriptor.id, response, action="failed"),
                provider=self.descriptor.id,
            )

        piper_v = response.headers.get("X-Piper-Version")
        piper_eng = response.headers.get("X-Piper-Engine")
        if piper_v and self._version_cache is None:
            self._version_cache = {
                "version": piper_v,
                "piper": piper_v,
                "engine": piper_eng or "piper-onnx",
            }

        return response.content

    def _server_versions(self, client: httpx.Client | None = None) -> dict[str, str]:
        if self._version_cache is not None:
            return self._version_cache

        if client is not None:
            try:
                resp = client.get(VERSION_PATH, timeout=5.0)
                if resp.is_success:
                    self._version_cache = {str(k): str(v) for k, v in resp.json().items()}
                    return self._version_cache
            except Exception:
                pass
        return {"version": self.descriptor.version}

    def versions(self) -> dict[str, str]:
        if self._version_cache is not None:
            return {"provider": self.descriptor.id, **self._version_cache}
        with self._client(timeout=5.0) as client:
            srv = self._server_versions(client)
            return {"provider": self.descriptor.id, **srv}

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            with self._client(timeout=5.0) as client:
                resp = client.get(self.descriptor.health_path)
                healthy = resp.is_success
                detail = None if healthy else f"HTTP {resp.status_code}"
                version = (
                    resp.json().get("version", self.descriptor.version)
                    if healthy
                    else self.descriptor.version
                )
        except Exception as exc:
            healthy = False
            detail = f"sidecar unreachable: {exc}"
            version = self.descriptor.version

        return ProviderHealth(
            provider=self.descriptor.id,
            healthy=healthy,
            version=version,
            detail=detail,
            checked_at=checked_at,
        )


__all__ = ["PiperTtsProvider"]
