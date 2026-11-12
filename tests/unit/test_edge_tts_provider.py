"""Unit tests for EdgeTtsProvider."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acessilia_toolbox.core.errors import InvalidInputError, ProviderUnavailableError
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers import create_adapter
from acessilia_toolbox.providers.edge_tts_provider import EdgeTtsProvider


def make_descriptor(**overrides: object) -> ProviderDescriptor:
    base = {
        "id": "edge-tts",
        "version": "7.3",
        "transport": "in_process",
        "capabilities": ["audio.synthesize"],
        "timeout_seconds": 300,
        "config": {
            "default_voice": "pt-BR-FranciscaNeural",
            "default_language": "pt-BR",
        },
    }
    return ProviderDescriptor.model_validate({**base, **overrides})


class TestEdgeTtsProvider:
    def test_registry_creates_edge_tts(self) -> None:
        adapter = create_adapter(make_descriptor())
        assert isinstance(adapter, EdgeTtsProvider)

    def test_rejects_unknown_capability(self) -> None:
        provider = EdgeTtsProvider(make_descriptor())
        with pytest.raises(ValueError, match="Unsupported capability"):
            provider.execute(
                "document.structure.extract",
                b"Hello world",
                filename="doc.txt",
                media_type="text/plain",
            )

    def test_rejects_empty_payload(self) -> None:
        provider = EdgeTtsProvider(make_descriptor())
        with pytest.raises(InvalidInputError, match="no content"):
            provider.execute(
                "audio.synthesize",
                b"   \n\n  ",
                filename="doc.txt",
                media_type="text/plain",
            )

    def test_rejects_invalid_utf8(self) -> None:
        provider = EdgeTtsProvider(make_descriptor())
        with pytest.raises(InvalidInputError, match="UTF-8"):
            provider.execute(
                "audio.synthesize",
                b"\xff\xfe\x00\x00",
                filename="doc.txt",
                media_type="text/plain",
            )

    def test_chunk_text_paragraphs(self) -> None:
        provider = EdgeTtsProvider(make_descriptor())
        p1 = "Paragraph 1 " * 50
        p2 = "Paragraph 2 " * 50
        text = f"{p1}\n\n{p2}"

        chunks = provider._chunk_text(text, chunk_size=200)
        assert len(chunks) == 2
        assert "Paragraph 1" in chunks[0]
        assert "Paragraph 2" in chunks[1]

    def test_synthesize_success_mocked(self) -> None:
        provider = EdgeTtsProvider(make_descriptor())

        mock_communicate_cls = MagicMock()
        mock_instance = MagicMock()

        async def fake_save(output_path: str) -> None:
            Path(output_path).write_bytes(b"FAKE_MP3_FRAME_DATA")

        mock_instance.save = AsyncMock(side_effect=fake_save)
        mock_communicate_cls.return_value = mock_instance

        mock_edge_tts = MagicMock()
        mock_edge_tts.Communicate = mock_communicate_cls
        mock_edge_tts.__version__ = "7.3.1"

        with patch.dict("sys.modules", {"edge_tts": mock_edge_tts}):
            result = provider.execute(
                "audio.synthesize",
                b"Primeiro paragrafo.\n\nSegundo paragrafo.",
                filename="speech_sample.txt",
                media_type="text/plain",
                parameters={"voice": "pt-BR-AntonioNeural", "rate": "+10%", "chunk_size": 25},
            )

        assert result.backend == "edge-tts"
        assert result.document["media_type"] == "audio/mpeg"
        assert result.document["voice"] == "pt-BR-AntonioNeural"
        assert result.document["language"] == "pt-BR"
        assert result.document["chunk_count"] == 2
        assert result.document["size_bytes"] == len(b"FAKE_MP3_FRAME_DATA" * 2)
        assert result.document["toolchain"]["provider"] == "edge-tts"
        assert result.document["toolchain"]["edge_tts"] == "7.3.1"

        assert len(result.outputs) == 1
        output = result.outputs[0]
        assert output.filename == "speech_sample.mp3"
        assert output.media_type == "audio/mpeg"
        assert output.payload == b"FAKE_MP3_FRAME_DATA" * 2

    def test_provider_unavailable_when_missing(self) -> None:
        provider = EdgeTtsProvider(make_descriptor())
        with (
            patch.dict("sys.modules", {"edge_tts": None}),
            pytest.raises(ProviderUnavailableError, match="edge-tts is not installed"),
        ):
            provider.execute(
                    "audio.synthesize",
                    b"Some text",
                    filename="doc.txt",
                    media_type="text/plain",
                )

    def test_health_and_versions(self) -> None:
        provider = EdgeTtsProvider(make_descriptor())
        versions = provider.versions()
        assert versions["provider"] == "edge-tts"

        health = provider.health()
        assert health.provider == "edge-tts"
        assert isinstance(health.healthy, bool)

    def test_schema_valid_json(self) -> None:
        schema_path = Path("schemas/audio-export@1.json")
        assert schema_path.is_file()
        data = json.loads(schema_path.read_text(encoding="utf-8"))
        assert data["$id"] == "urn:a11y-devs:schema:audio-export:1.0.0"
        assert "audio/mpeg" in data["properties"]["media_type"]["enum"]

    def test_executor_end_to_end_by_reference(self, tmp_path: Path) -> None:
        from acessilia_toolbox.core.capability import CapabilityRegistry
        from acessilia_toolbox.core.executor import CapabilityExecutor
        from acessilia_toolbox.core.provider import ProviderRegistry
        from acessilia_toolbox.providers.storage import FilesystemArtifactStore

        cap_registry = CapabilityRegistry.from_directory(Path("capabilities"))
        prov_registry = ProviderRegistry.from_file(Path("providers-config.yaml"))
        store = FilesystemArtifactStore(tmp_path / "artifacts")

        executor = CapabilityExecutor(
            capabilities=cap_registry,
            providers=prov_registry,
            adapter_factory=create_adapter,
            store=store,
        )

        mock_communicate_cls = MagicMock()
        mock_instance = MagicMock()

        async def fake_save(output_path: str) -> None:
            Path(output_path).write_bytes(b"ID3_REAL_MP3_PAYLOAD")

        mock_instance.save = AsyncMock(side_effect=fake_save)
        mock_communicate_cls.return_value = mock_instance

        mock_edge_tts = MagicMock()
        mock_edge_tts.Communicate = mock_communicate_cls
        mock_edge_tts.__version__ = "7.3.1"

        with patch.dict("sys.modules", {"edge_tts": mock_edge_tts}):
            result = executor.execute(
                "audio.synthesize",
                b"Capitulo 1. Introducao ao sistema acessivel.",
                filename="capitulo1.txt",
                media_type="text/plain",
                provider_id="edge-tts",
            )

        assert result.status == "succeeded"
        assert result.capability == "audio.synthesize@1"
        assert result.provider == "edge-tts"
        # Two artifacts: document json (metadata) + audio binary
        assert len(result.artifacts) == 2

        mp3_artifact = result.artifacts[1]
        assert mp3_artifact.media_type == "audio/mpeg"
        stored_bytes = store.get(mp3_artifact.artifact_id)
        assert stored_bytes == b"ID3_REAL_MP3_PAYLOAD"

