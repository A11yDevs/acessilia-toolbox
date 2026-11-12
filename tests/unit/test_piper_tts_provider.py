"""Unit tests for PiperTtsProvider."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from acessilia_toolbox.core.errors import InvalidInputError, ProviderUnavailableError
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.piper_tts_provider import PiperTtsProvider


def make_descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "piper-tts",
        "version": "1.2",
        "transport": "in_process",
        "capabilities": ["audio.synthesize"],
        "timeout_seconds": 300,
        "config": {
            "default_voice": "pt_BR-faber-medium",
            "default_language": "pt-BR",
        },
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


class TestPiperTtsProvider:
    def test_registry_creates_piper_tts(self) -> None:
        adapter = create_adapter(make_descriptor())
        assert isinstance(adapter, PiperTtsProvider)

    def test_rejects_unknown_capability(self) -> None:
        provider = PiperTtsProvider(make_descriptor())
        with pytest.raises(ValueError, match="Unsupported capability"):
            provider.execute(
                "document.structure.extract",
                b"Hello world",
                filename="doc.txt",
                media_type="text/plain",
            )

    def test_rejects_empty_payload(self) -> None:
        provider = PiperTtsProvider(make_descriptor())
        with pytest.raises(InvalidInputError, match="no content"):
            provider.execute(
                "audio.synthesize",
                b"   \n\n  ",
                filename="doc.txt",
                media_type="text/plain",
            )

    def test_rejects_invalid_utf8(self) -> None:
        provider = PiperTtsProvider(make_descriptor())
        with pytest.raises(InvalidInputError, match="UTF-8"):
            provider.execute(
                "audio.synthesize",
                b"\xff\xfe\x00\x00",
                filename="doc.txt",
                media_type="text/plain",
            )

    def test_provider_unavailable_when_piper_missing(self) -> None:
        provider = PiperTtsProvider(make_descriptor())
        with (
            patch.dict("sys.modules", {"piper": None, "piper.voice": None}),
            pytest.raises(ProviderUnavailableError, match="piper-tts is not installed"),
        ):
            provider.execute(
                    "audio.synthesize",
                    b"Some text",
                    filename="doc.txt",
                    media_type="text/plain",
                )

    def test_provider_unavailable_when_model_missing(self, tmp_path: Path) -> None:
        provider = PiperTtsProvider(make_descriptor())
        mock_piper = MagicMock()
        mock_voice_mod = MagicMock()

        with patch.dict("sys.modules", {"piper": mock_piper, "piper.voice": mock_voice_mod}):
            # Case 1: no model_path
            with pytest.raises(ProviderUnavailableError, match="specify model_path"):
                provider.execute(
                    "audio.synthesize",
                    b"Some text",
                    filename="doc.txt",
                    media_type="text/plain",
                )

            # Case 2: non-existent model file
            non_existent = str(tmp_path / "missing.onnx")
            with pytest.raises(ProviderUnavailableError, match="model file not found"):
                provider.execute(
                    "audio.synthesize",
                    b"Some text",
                    filename="doc.txt",
                    media_type="text/plain",
                    parameters={"model_path": non_existent},
                )

    def test_synthesize_success_mocked(self, tmp_path: Path) -> None:
        provider = PiperTtsProvider(make_descriptor())

        model_file = tmp_path / "voice.onnx"
        model_file.write_bytes(b"FAKE_ONNX_MODEL")

        mock_piper = MagicMock()
        mock_piper.__version__ = "1.2.0"
        mock_voice_mod = MagicMock()

        mock_voice_instance = MagicMock()

        def fake_synthesize_wav(text: str, wav_file: Any, **kwargs: Any) -> None:
            # write fake wav header + data
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"FAKE_PCM_AUDIO_DATA")

        mock_voice_instance.synthesize_wav = fake_synthesize_wav
        mock_voice_mod.PiperVoice.load.return_value = mock_voice_instance

        with patch.dict("sys.modules", {"piper": mock_piper, "piper.voice": mock_voice_mod}):
            result = provider.execute(
                "audio.synthesize",
                b"Texto de teste para sintese offline.",
                filename="document.txt",
                media_type="text/plain",
                parameters={
                    "model_path": str(model_file),
                    "voice": "pt_BR-faber-medium",
                    "speaker_id": 0,
                },
            )

        assert result.backend == "piper-tts"
        assert result.document["media_type"] == "audio/wav"
        assert result.document["voice"] == "pt_BR-faber-medium"
        assert result.document["language"] == "pt-BR"
        assert result.document["size_bytes"] > 0
        assert result.document["toolchain"]["provider"] == "piper-tts"
        assert result.document["toolchain"]["piper"] == "1.2.0"

        assert len(result.outputs) == 1
        output = result.outputs[0]
        assert output.filename == "document.wav"
        assert output.media_type == "audio/wav"
        assert len(output.payload) > 0

    def health_and_versions_check(self) -> None:
        provider = PiperTtsProvider(make_descriptor())
        versions = provider.versions()
        assert versions["provider"] == "piper-tts"

        health = provider.health()
        assert health.provider == "piper-tts"
