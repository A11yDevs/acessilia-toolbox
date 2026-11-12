"""Adapter for Microsoft Edge text-to-speech service (edge-tts).

Synthesizes plain text or markdown content into spoken audio using Microsoft's
edge-tts library, providing 1:1 parity with the legacy core audio exporter
(A11yDevs/acessilia#382).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, ClassVar, cast

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

_ASYNC_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def _normalize_rate(val: Any) -> str | None:
    """Normalize speech rate to Edge TTS format (e.g. '+20%', '-10%')."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if isinstance(val, float) and 0.0 < val < 5.0 and val != 1.0:
            pct = round((val - 1.0) * 100)
            return f"{pct:+d}%"
        pct = round(val)
        return f"{pct:+d}%"
    s = str(val).strip()
    if not s:
        return None
    if not (s.startswith("+") or s.startswith("-")):
        s = f"+{s}"
    if not s.endswith("%"):
        s = f"{s}%"
    return s


def _normalize_volume(val: Any) -> str | None:
    """Normalize speech volume to Edge TTS format (e.g. '+50%', '-20%')."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if isinstance(val, float) and 0.0 <= val <= 2.0 and val != 1.0:
            pct = round((val - 1.0) * 100)
            return f"{pct:+d}%"
        pct = round(val)
        return f"{pct:+d}%"
    s = str(val).strip()
    if not s:
        return None
    if not (s.startswith("+") or s.startswith("-")):
        s = f"+{s}"
    if not s.endswith("%"):
        s = f"{s}%"
    return s


def _normalize_pitch(val: Any) -> str | None:
    """Normalize speech pitch to Edge TTS format (e.g. '+50Hz', '-10Hz')."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        pct = round(val)
        return f"{pct:+d}Hz"
    s = str(val).strip()
    if not s:
        return None
    if not (s.startswith("+") or s.startswith("-")):
        s = f"+{s}"
    if not (s.endswith("Hz") or s.endswith("%")):
        s = f"{s}Hz"
    return s


def _split_long_paragraph(paragraph: str, max_size: int) -> list[str]:
    """Split a single oversized paragraph into sub-chunks on sentence/word boundaries."""
    if len(paragraph) <= max_size:
        return [paragraph]

    parts: list[str] = []
    raw_sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    curr = ""

    for s in raw_sentences:
        if len(s) > max_size:
            words = s.split(" ")
            w_curr = ""
            for w in words:
                if len(w) > max_size:
                    if w_curr:
                        parts.append(w_curr)
                        w_curr = ""
                    for i in range(0, len(w), max_size):
                        parts.append(w[i : i + max_size])
                elif len(w_curr) + len(w) + 1 <= max_size:
                    w_curr = f"{w_curr} {w}" if w_curr else w
                else:
                    if w_curr:
                        parts.append(w_curr)
                    w_curr = w
            if w_curr:
                parts.append(w_curr)
        elif len(curr) + len(s) + 1 <= max_size:
            curr = f"{curr} {s}" if curr else s
        else:
            if curr:
                parts.append(curr)
            curr = s

    if curr:
        parts.append(curr)

    return parts


class EdgeTtsProvider:
    """Synthesizes text into spoken MP3 audio via edge-tts."""

    supports_streaming_input = True
    backend: ClassVar[str] = "edge-tts"

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
        language = str(
            params.get("language")
            or config.get("default_language")
            or DEFAULT_LANGUAGE
        )
        rate = _normalize_rate(params.get("rate"))
        volume = _normalize_volume(params.get("volume"))
        pitch = _normalize_pitch(params.get("pitch"))
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
        expanded: list[str] = []
        for p in paragraphs:
            if len(p) > chunk_size:
                expanded.extend(_split_long_paragraph(p, chunk_size))
            else:
                expanded.append(p)

        chunks: list[str] = []
        current_chunk = ""

        for p in expanded:
            if not p.strip():
                continue
            if len(current_chunk) + len(p) + 1 < chunk_size:
                current_chunk = f"{current_chunk}\n{p}" if current_chunk else p
            else:
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                current_chunk = p

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
            return cast(bytes, _ASYNC_EXECUTOR.submit(asyncio.run, coro).result())
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
