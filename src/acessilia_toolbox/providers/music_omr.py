"""Optical Music Recognition (OMR) adapters.

Two providers back the ``music.omr`` capability:

- ``homr``: pure-Python, runs the ``homr`` package in-process to convert
  sheet-music images into MusicXML/Mei.
- ``audiveris``: HTTP adapter for the ``audiveris-serve`` sidecar container
  that wraps the Java Audiveris CLI.

Both return a canonical dict matching ``artifact/music-score@1``.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
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

HOMR_VERSION = "0.1.0"
AUDIVERIS_VERSION = "0.1.0"

SCORE_MEDIA_TYPES = ("application/vnd.recordare.musicxml+xml", "application/xml")


# ---------------------------------------------------------------------------
# homr (in-process)
# ---------------------------------------------------------------------------


class HomrProvider:
    """In-process OMR via the homr package (or its CLI if installed).

    The homr library is optional; without it the provider reports unhealthy
    and ``execute`` raises a clear error instead of failing obscurely.
    """

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
        if capability_id != "music.omr":
            raise ValueError(f"Unsupported capability: {capability_id}")

        started_at = datetime.now(UTC)
        started_clock = perf_counter()
        params = dict(parameters or {})
        output_format = str(params.get("output_format", "musicxml")).lower()
        if output_format not in ("musicxml", "mei"):
            raise ProviderExecutionError(
                f"unsupported output_format: {output_format}",
                provider=self.descriptor.id,
            )

        try:
            import homr  # type: ignore[import-untyped,unused-ignore]  # noqa: F401
        except ImportError as exc:
            raise ProviderUnavailableError(
                "homr is not installed; install the 'music' extra "
                "(pip install .[music])",
                provider=self.descriptor.id,
            ) from exc

        score = self._run_homr(payload, filename, output_format)
        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document=score,
            backend="homr",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=round((perf_counter() - started_clock) * 1000),
            version=HOMR_VERSION,
            configuration={
                "capability": capability_id,
                "output_format": output_format,
                **params,
            },
        )

    def _run_homr(self, payload: bytes, filename: str, output_format: str) -> dict[str, Any]:
        """Write the image to a temp dir and invoke the homr pipeline."""
        with TemporaryDirectory(prefix="toolbox-homr-") as tmp:
            workdir = Path(tmp)
            image_path = workdir / filename
            image_path.write_bytes(payload)

            try:
                from homr.main import run  # type: ignore[import-untyped,unused-ignore]
            except ImportError as exc:
                raise ProviderUnavailableError(
                    "homr package layout changed; cannot import homr.main.run",
                    provider=self.descriptor.id,
                ) from exc

            try:
                result_path = run(str(image_path))
            except Exception as exc:
                raise ProviderExecutionError(
                    f"homr failed: {type(exc).__name__}: {exc}",
                    provider=self.descriptor.id,
                ) from exc

            score_path = Path(str(result_path))
            if not score_path.is_file():
                # homr may return a directory of artifacts; pick the score.
                candidates = sorted(score_path.rglob(f"*.{output_format}"))
                if not candidates:
                    raise ProviderExecutionError(
                        f"homr produced no {output_format} output",
                        provider=self.descriptor.id,
                    )
                score_path = candidates[0]

            notation = score_path.read_text(encoding="utf-8", errors="replace")

        return {
            "notation": notation,
            "format": output_format,
            "media_type": SCORE_MEDIA_TYPES[0],
            "confidence": None,
            "source_filename": filename,
        }

    def versions(self) -> dict[str, str]:
        return {"provider": HOMR_VERSION}

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            import homr  # noqa: F401

            available = True
            detail = None
        except ImportError:
            available = False
            detail = "homr package not installed"
        return ProviderHealth(
            provider=self.descriptor.id,
            healthy=available,
            version=HOMR_VERSION,
            detail=detail,
            checked_at=checked_at,
        )


# ---------------------------------------------------------------------------
# Audiveris (HTTP sidecar)
# ---------------------------------------------------------------------------

CONVERT_PATH = "/convert"


class AudiverisProvider:
    """Calls the audiveris-serve sidecar (POST /convert multipart)."""

    def __init__(self, descriptor: ProviderDescriptor) -> None:
        self.descriptor = descriptor
        self.base_url = (descriptor.endpoint or "").rstrip("/")

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str,
        media_type: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        if capability_id != "music.omr":
            raise ValueError(f"Unsupported capability: {capability_id}")

        started_at = datetime.now(UTC)
        started_clock = perf_counter()
        params = dict(parameters or {})
        data = {"output_format": str(params.get("output_format", "musicxml"))}

        try:
            with self._client() as client:
                response = client.post(
                    CONVERT_PATH,
                    files={"files": (filename, payload, media_type)},
                    data=data,
                )
                response.raise_for_status()
                body = response.json()
                version = self._server_version(client)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"audiveris-serve timed out after "
                f"{self.descriptor.timeout_seconds}s",
                provider=self.descriptor.id,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderExecutionError(
                f"audiveris-serve rejected: HTTP {exc.response.status_code}",
                provider=self.descriptor.id,
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                f"audiveris-serve unreachable at {self.base_url}",
                provider=self.descriptor.id,
            ) from exc

        if not isinstance(body, dict):
            raise ProviderExecutionError(
                "audiveris-serve returned an unexpected payload",
                provider=self.descriptor.id,
            )

        completed_at = datetime.now(UTC)
        return ExtractionResult(
            document={
                "notation": body.get("notation", ""),
                "format": body.get("format", "musicxml"),
                "media_type": SCORE_MEDIA_TYPES[0],
                "confidence": body.get("confidence"),
                "source_filename": filename,
            },
            backend="audiveris",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=round((perf_counter() - started_clock) * 1000),
            version=version or self.descriptor.version,
            configuration={"capability": capability_id, **params},
        )

    def versions(self) -> dict[str, str]:
        with self._client(timeout=10.0) as client:
            version = self._server_version(client)
        return {"provider": version or self.descriptor.version}

    def health(self) -> ProviderHealth:
        checked_at = datetime.now(UTC)
        try:
            with self._client(timeout=10.0) as client:
                response = client.get(self.descriptor.health_path)
                response.raise_for_status()
                return ProviderHealth(
                    provider=self.descriptor.id,
                    healthy=True,
                    version=self._server_version(client)
                    or self.descriptor.version,
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

    def _server_version(self, client: httpx.Client) -> str | None:
        try:
            response = client.get("/version")
            response.raise_for_status()
            data = response.json()
        except Exception:
            return None
        if isinstance(data, dict):
            version = data.get("version") or data.get("audiveris")
            return str(version) if version else None
        return None


# ---------------------------------------------------------------------------
# Shared: MusicXML sanity helper used by tests and sidecars
# ---------------------------------------------------------------------------


def looks_like_music_document(notation: str, output_format: str) -> bool:
    """Cheap structural check that the OMR output parses as XML."""
    if not notation.strip():
        return False
    try:
        import xml.etree.ElementTree as ET

        ET.fromstring(notation)
    except ET.ParseError:
        return False
    return True


def audiveris_cli_available() -> bool:
    """True when the Audiveris CLI exists on PATH (for sidecar dev checks)."""
    return shutil.which("Audiveris") is not None


__all__ = [
    "AudiverisProvider",
    "HomrProvider",
    "audiveris_cli_available",
    "looks_like_music_document",
]
