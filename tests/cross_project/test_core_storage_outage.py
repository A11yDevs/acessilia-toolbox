"""Core and SDK against a real Toolbox HTTP server during storage failure.

Run with ACESSILIA_CORE_PYTHON and ACESSILIA_CORE_ROOT pointing at a separate
Core environment/checkout. Extraction and S3 are stubbed; orchestration,
multipart transport, REST execution and inline output handling are real.
"""

from __future__ import annotations

import os
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from unittest.mock import Mock

import pytest
import uvicorn

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.artifact import NullCache
from acessilia_toolbox.core.capability import CapabilityManifest, CapabilityRegistry
from acessilia_toolbox.core.errors import ProviderUnavailableError
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.getenv("ACESSILIA_CORE_PYTHON"), reason="requires a separate Core environment"
    ),
]

CORE_CHECK = """
import asyncio
import sys
from pathlib import Path
from acessilia_sdk.toolbox.clients import ToolboxArtifactClient, ToolboxStructureClient
from backend.core.manifest.toolbox_extractor import ToolboxManifestExtractor

async def main():
    url, filename = sys.argv[1:]
    artifacts = ToolboxArtifactClient(base_url=url, api_key="storage-outage-test", retries=0)
    structure = ToolboxStructureClient(
        base_url=url, api_key="storage-outage-test", provider="docling", retries=0
    )
    try:
        result = await ToolboxManifestExtractor(
            client=structure, artifact_client=artifacts, use_artifact_store=True
        )._extract_async(Path(filename), dual_fusion=False)
        assert result.document["document"]["text"] == "extraction completed"
        assert result.configuration["artifact_upload_error"]
        assert result.configuration["use_artifact_store"] is False
        assert result.artifact_id is None
        ref = result.document["artifacts"][0]
        assert ref["storage_backend"] is None and ref["uri"] is None
        print("Core extraction succeeded after upload and JSON persistence failed")
    finally:
        await artifacts.close()
        await structure.close()

asyncio.run(main())
"""


def test_core_direct_extraction_survives_json_storage_outage(tmp_path, monkeypatch):
    from acessilia_toolbox.api import auth

    monkeypatch.setattr(auth, "TOOLBOX_API_KEY", "storage-outage-test")
    provider = Mock()
    now = datetime.now(UTC)
    provider.versions.return_value = {"provider": "test"}
    provider.execute.return_value = ExtractionResult(
        document={"pages": [], "elements": [], "text": "extraction completed"},
        backend="docling", started_at=now, completed_at=now, duration_ms=1, version="test",
    )
    store = Mock()
    store.put.side_effect = ProviderUnavailableError("S3 offline; local fallback disabled")
    store.put_stream.side_effect = ProviderUnavailableError("S3 offline; local fallback disabled")
    app = create_app(
        capabilities=CapabilityRegistry([CapabilityManifest.model_validate({
            "id": "document.structure.extract", "version": 1, "description": "Test extraction",
            "input": {"schema": "input", "media_types": ["application/pdf"]},
            "output": {"schema": "output"},
        })]),
        providers=ProviderRegistry([ProviderDescriptor(
            id="docling", capabilities=["document.structure.extract"],
        )]),
        store=store, cache=NullCache(),
    )
    app.state.executor._adapter_factory = lambda _: provider
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-test")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 10
            while not server.started and thread.is_alive() and monotonic() < deadline:
                sleep(0.01)
            assert server.started, "Toolbox HTTP server did not start"
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            completed = subprocess.run(
                [os.environ["ACESSILIA_CORE_PYTHON"], "-c", CORE_CHECK, url, str(source)],
                cwd=Path(os.environ["ACESSILIA_CORE_ROOT"]),
                capture_output=True, text=True, timeout=60, check=False,
            )
            assert completed.returncode == 0, completed.stdout + completed.stderr
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()
    assert store.put_stream.call_count == 1  # Streaming input upload.
    assert store.put.call_count == 1  # Output JSON persistence.
    provider.execute.assert_called_once()
