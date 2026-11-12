"""Unit tests for PiperTtsProvider (HTTP sidecar adapter)."""

from __future__ import annotations

import httpx
import pytest

from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderExecutionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.piper_tts_provider import PiperTtsProvider

FAKE_WAV_HEADER = (
    b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"\x22\x56\x00\x00\x44\xac\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
)


def make_descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "piper-tts",
        "version": "1.2",
        "transport": "http",
        "endpoint": "http://piper-tts:5012",
        "capabilities": ["audio.synthesize"],
        "timeout_seconds": 5.0,
        "config": {
            "default_voice": "pt_BR-faber-medium",
            "default_language": "pt-BR",
        },
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


def provider_with(handler) -> PiperTtsProvider:
    adapter = PiperTtsProvider(make_descriptor())
    transport = httpx.MockTransport(handler)
    adapter._client = lambda timeout=None: httpx.Client(  # type: ignore[method-assign]
        transport=transport, base_url=adapter.base_url
    )
    return adapter


class TestPiperTtsProvider:
    def test_registry_creates_piper_tts(self) -> None:
        adapter = create_adapter(make_descriptor())
        assert isinstance(adapter, PiperTtsProvider)
        assert adapter.base_url == "http://piper-tts:5012"

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

    def test_synthesize_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/version":
                return httpx.Response(
                    200,
                    json={
                        "version": "1.2.0",
                        "piper": "1.2.0",
                        "engine": "piper-onnx",
                    },
                )
            if request.url.path == "/synthesize":
                # Verify form body
                content_str = request.content.decode("utf-8")
                assert "text=" in content_str
                assert "voice=" in content_str
                return httpx.Response(200, content=FAKE_WAV_HEADER)
            return httpx.Response(404)

        provider = provider_with(handler)
        result = provider.execute(
            "audio.synthesize",
            b"Texto para falar em voz alta via sidecar Docker.",
            filename="documento.txt",
            media_type="text/plain",
            parameters={"voice": "pt_BR-edresson-low", "length_scale": 1.1},
        )

        assert result.backend == "piper-tts"
        assert result.document["media_type"] == "audio/wav"
        assert result.document["voice"] == "pt_BR-edresson-low"
        assert result.document["language"] == "pt-BR"
        assert result.document["size_bytes"] == len(FAKE_WAV_HEADER)
        assert result.document["toolchain"]["provider"] == "piper-tts"
        assert result.document["toolchain"]["engine"] == "piper-onnx"

        assert len(result.outputs) == 1
        output = result.outputs[0]
        assert output.filename == "documento.wav"
        assert output.media_type == "audio/wav"
        assert output.payload == FAKE_WAV_HEADER

    def test_sidecar_400_raises_invalid_input(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"detail": "Bad text input"})

        provider = provider_with(handler)
        with pytest.raises(InvalidInputError, match="Bad text input"):
            provider.execute(
                "audio.synthesize",
                b"invalid input",
                filename="doc.txt",
                media_type="text/plain",
            )

    def test_sidecar_503_raises_provider_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"detail": "Model loading"})

        provider = provider_with(handler)
        with pytest.raises(ProviderUnavailableError, match="Model loading"):
            provider.execute(
                "audio.synthesize",
                b"some text",
                filename="doc.txt",
                media_type="text/plain",
            )

    def test_sidecar_500_raises_provider_execution_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "Inference exploded"})

        provider = provider_with(handler)
        with pytest.raises(ProviderExecutionError, match="Inference exploded"):
            provider.execute(
                "audio.synthesize",
                b"some text",
                filename="doc.txt",
                media_type="text/plain",
            )

    def test_timeout_raises_provider_timeout_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        provider = provider_with(handler)
        with pytest.raises(ProviderTimeoutError, match="timed out"):
            provider.execute(
                "audio.synthesize",
                b"some text",
                filename="doc.txt",
                media_type="text/plain",
            )

    def test_connection_error_raises_provider_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        provider = provider_with(handler)
        with pytest.raises(ProviderUnavailableError, match="unreachable"):
            provider.execute(
                "audio.synthesize",
                b"some text",
                filename="doc.txt",
                media_type="text/plain",
            )

    def test_health_and_versions(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok", "version": "1.2.0"})
            if request.url.path == "/version":
                return httpx.Response(200, json={"version": "1.2.0", "piper": "1.2.0"})
            return httpx.Response(404)

        provider = provider_with(handler)
        versions = provider.versions()
        assert versions["version"] == "1.2.0"
        assert versions["piper"] == "1.2.0"

        health = provider.health()
        assert health.provider == "piper-tts"
        assert health.healthy is True
        assert health.version == "1.2.0"
