"""In-process text-to-speech provider using Piper (offline ONNX neural TTS).

Enables self-hosted, offline, CPU-optimized speech synthesis with pre-trained models
(such as pt-BR Faber or Edresson). Produces a WAV audio artifact.
"""

from __future__ import annotations

import io
import logging
import wave
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderExecutionError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.normalization.extraction import BinaryOutput, ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

LOG = logging.getLogger(__name__)

DEFAULT_PIPER_VOICE = "pt_BR-faber-medium"
DEFAULT_PIPER_LANGUAGE = "pt-BR"
PIPER_VERSION = "1.2.0"


class PiperTtsProvider:
    """Synthesizes text into spoken audio via Piper (ONNX)."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor

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
        model_path = params.get("model_path") or config.get("model_path")
        config_path = params.get("config_path") or config.get("config_path")
        speaker_id = params.get("speaker_id")
        length_scale = params.get("length_scale")
        noise_scale = params.get("noise_scale")
        noise_w = params.get("noise_w")

        try:
            import piper  # type: ignore[import,unused-ignore]
            from piper.config import SynthesisConfig  # type: ignore[import,unused-ignore]
            from piper.voice import PiperVoice  # type: ignore[import,unused-ignore]
        except ImportError as exc:
            raise ProviderUnavailableError(
                "piper-tts is not installed; install with pip install acessilia-toolbox[audio]",
                provider=self.descriptor.id,
            ) from exc

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

        if not model_path:
            raise ProviderUnavailableError(
                f"piper voice model '{voice}' not found: "
                "specify model_path in configuration or parameters",
                provider=self.descriptor.id,
            )

        model_file = Path(str(model_path))
        if not model_file.is_file():
            raise ProviderUnavailableError(
                f"piper voice model file not found at {model_file}",
                provider=self.descriptor.id,
            )

        try:
            piper_voice = PiperVoice.load(
                str(model_file),
                config_path=str(config_path) if config_path else None,
            )
        except Exception as exc:
            raise ProviderExecutionError(
                f"failed to load piper model: {exc}", provider=self.descriptor.id
            ) from exc

        syn_config = SynthesisConfig(
            speaker_id=int(speaker_id) if speaker_id is not None else None,
            length_scale=float(length_scale) if length_scale is not None else None,
            noise_scale=float(noise_scale) if noise_scale is not None else None,
            noise_w_scale=float(noise_w) if noise_w is not None else None,
        )

        wav_io = io.BytesIO()
        try:
            with wave.open(wav_io, "wb") as wav_file:
                piper_voice.synthesize_wav(clean_text, wav_file, syn_config=syn_config)
        except Exception as exc:
            raise ProviderExecutionError(
                f"piper synthesis failed: {exc}", provider=self.descriptor.id
            ) from exc

        audio_bytes = wav_io.getvalue()
        completed_at = datetime.now(UTC)
        duration_ms = round((perf_counter() - started_clock) * 1000)
        piper_version = getattr(piper, "__version__", PIPER_VERSION)

        document = {
            "artifact_id": fingerprint_bytes(audio_bytes),
            "media_type": "audio/wav",
            "size_bytes": len(audio_bytes),
            "voice": voice,
            "language": language,
            "toolchain": {
                "provider": self.descriptor.id,
                "piper": piper_version,
            },
        }

        stem = Path(filename).stem or "audio"
        binary_output = BinaryOutput(
            payload=audio_bytes,
            media_type="audio/wav",
            filename=f"{stem}.wav",
        )

        return ExtractionResult(
            document=document,
            backend=self.descriptor.id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            version=piper_version,
            configuration={
                "capability": capability_id,
                "voice": voice,
                "language": language,
                **params,
            },
            outputs=(binary_output,),
        )

    def versions(self) -> dict[str, str]:
        try:
            import piper
            v = getattr(piper, "__version__", PIPER_VERSION)
            return {"provider": self.descriptor.id, "piper": v}
        except ImportError:
            return {"provider": self.descriptor.id}

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            import piper
            v = getattr(piper, "__version__", PIPER_VERSION)
            available = True
            detail = None
        except ImportError:
            v = PIPER_VERSION
            available = False
            detail = "piper-tts package not installed"

        return ProviderHealth(
            provider=self.descriptor.id,
            healthy=available,
            version=v,
            detail=detail,
            checked_at=checked_at,
        )


__all__ = ["PiperTtsProvider"]
