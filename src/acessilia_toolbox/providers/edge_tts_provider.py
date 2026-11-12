"""In-process text-to-speech provider using Microsoft Edge TTS (edge-tts).

Provides 1:1 parity with the legacy core operational executor (Acessilia #73).
Paragraph-based chunking with semaphore concurrency handles long documents cleanly,
producing an assembled MP3 artifact.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from acessilia_toolbox.core.errors import (
    InvalidInputError,
    ProviderExecutionError,
    ProviderUnavailableError,
)
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.normalization.extraction import BinaryOutput, ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

LOG = logging.getLogger(__name__)

DEFAULT_VOICE = "pt-BR-FranciscaNeural"
DEFAULT_LANGUAGE = "pt-BR"
DEFAULT_CHUNK_SIZE = 1500
DEFAULT_CONCURRENCY = 5


class EdgeTtsProvider:
    """Synthesizes text into spoken MP3 audio via edge-tts."""

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
        voice = str(params.get("voice") or config.get("default_voice") or DEFAULT_VOICE)
        language = str(params.get("language") or config.get("default_language") or DEFAULT_LANGUAGE)
        rate = params.get("rate")
        volume = params.get("volume")
        pitch = params.get("pitch")
        chunk_size = int(params.get("chunk_size", DEFAULT_CHUNK_SIZE))
        concurrency = int(params.get("concurrency", DEFAULT_CONCURRENCY))

        try:
            import edge_tts
        except ImportError as exc:
            raise ProviderUnavailableError(
                "edge-tts is not installed; install with pip install acessilia-toolbox[audio]",
                provider=self.descriptor.id,
            ) from exc

        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidInputError(
                f"payload is not valid UTF-8 text: {exc}", capability=capability_id
            ) from exc

        chunks = self._chunk_text(text, chunk_size=chunk_size)
        if not chunks:
            raise InvalidInputError(
                "text payload contains no content to synthesize", capability=capability_id
            )

        mp3_bytes = self._synthesize_all(
            edge_tts=edge_tts,
            chunks=chunks,
            voice=voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
            concurrency=concurrency,
        )

        completed_at = datetime.now(UTC)
        duration_ms = round((perf_counter() - started_clock) * 1000)
        edge_tts_version = getattr(edge_tts, "__version__", self.descriptor.version)

        document = {
            "artifact_id": fingerprint_bytes(mp3_bytes),
            "media_type": "audio/mpeg",
            "size_bytes": len(mp3_bytes),
            "voice": voice,
            "language": language,
            "chunk_count": len(chunks),
            "toolchain": {
                "provider": self.descriptor.id,
                "edge_tts": edge_tts_version,
            },
        }

        stem = Path(filename).stem or "audio"
        binary_output = BinaryOutput(
            payload=mp3_bytes,
            media_type="audio/mpeg",
            filename=f"{stem}.mp3",
        )

        return ExtractionResult(
            document=document,
            backend=self.descriptor.id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            version=edge_tts_version,
            configuration={
                "capability": capability_id,
                "voice": voice,
                "language": language,
                "chunk_count": len(chunks),
                **params,
            },
            outputs=(binary_output,),
        )

    def _chunk_text(self, text: str, chunk_size: int) -> list[str]:
        """Split text by paragraphs into chunks bounded by chunk_size."""
        paragraphs = text.split("\n")
        chunks: list[str] = []
        current_chunk = ""

        for p in paragraphs:
            if len(current_chunk) + len(p) < chunk_size:
                current_chunk += p + "\n"
            else:
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                current_chunk = p + "\n"

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        if not chunks and text.strip():
            chunks = [text.strip()]

        return chunks

    def _synthesize_all(
        self,
        *,
        edge_tts: Any,
        chunks: list[str],
        voice: str,
        rate: str | None,
        volume: str | None,
        pitch: str | None,
        concurrency: int,
    ) -> bytes:
        """Run chunked async synthesis and concatenate resulting MP3 bytes."""
        async def _run() -> bytes:
            semaphore = asyncio.Semaphore(concurrency)

            with tempfile.TemporaryDirectory(prefix="toolbox-edge-tts-") as tmpdir:
                tmppath = Path(tmpdir)

                async def _process_chunk(idx: int, chunk_text: str) -> Path:
                    async with semaphore:
                        part_file = tmppath / f"part_{idx}.mp3"
                        extra_kwargs: dict[str, Any] = {}
                        if rate:
                            extra_kwargs["rate"] = rate
                        if volume:
                            extra_kwargs["volume"] = volume
                        if pitch:
                            extra_kwargs["pitch"] = pitch

                        communicate = edge_tts.Communicate(
                            chunk_text,
                            voice,
                            **extra_kwargs,
                        )
                        await communicate.save(str(part_file))
                        return part_file

                tasks = [
                    asyncio.create_task(_process_chunk(i, chunk))
                    for i, chunk in enumerate(chunks)
                ]
                try:
                    part_files = await asyncio.gather(*tasks)
                except Exception as exc:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    raise ProviderExecutionError(
                        f"edge-tts synthesis failed: {exc}", provider=self.descriptor.id
                    ) from exc

                combined = bytearray()
                for p in part_files:
                    combined.extend(p.read_bytes())
                return bytes(combined)

        return self._run_coroutine(_run())

    def _run_coroutine(self, coro: Any) -> bytes:
        """Execute an async coroutine from synchronous adapter code safely."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return cast(bytes, pool.submit(asyncio.run, coro).result())
        return cast(bytes, asyncio.run(coro))

    def versions(self) -> dict[str, str]:
        try:
            import edge_tts
            v = getattr(edge_tts, "__version__", self.descriptor.version)
            return {"provider": self.descriptor.id, "edge_tts": v}
        except ImportError:
            return {"provider": self.descriptor.id}

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            import edge_tts
            v = getattr(edge_tts, "__version__", self.descriptor.version)
            available = True
            detail = None
        except ImportError:
            v = self.descriptor.version
            available = False
            detail = "edge-tts package not installed"

        return ProviderHealth(
            provider=self.descriptor.id,
            healthy=available,
            version=v,
            detail=detail,
            checked_at=checked_at,
        )


__all__ = ["EdgeTtsProvider"]
