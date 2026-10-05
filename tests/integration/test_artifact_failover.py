"""Artifact storage falls back when the S3 endpoint cannot be reached."""

from __future__ import annotations

from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from time import monotonic

import pytest

from acessilia_toolbox.core.errors import ProviderUnavailableError
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.provider import ProviderDescriptor
from acessilia_toolbox.providers.storage import (
    FailoverArtifactStore,
    FilesystemArtifactStore,
    S3ArtifactStore,
)

pytestmark = pytest.mark.integration


def test_unreachable_s3_endpoint_uses_filesystem(tmp_path: Path) -> None:
    descriptor = ProviderDescriptor.model_validate(
        {
            "id": "offline-minio",
            "transport": "s3",
            "endpoint": "http://127.0.0.1:1",
            "capabilities": ["artifact.store"],
            "config": {"access_key": "test", "secret_key": "test"},
        }
    )
    primary = S3ArtifactStore(descriptor)
    store = FailoverArtifactStore(
        primary, FilesystemArtifactStore(tmp_path / "backup")
    )

    ref = store.put(b"stored during outage", media_type="text/plain")

    assert ref.storage_backend == "filesystem"
    assert store.get(ref.artifact_id) == b"stored during outage"
    assert store.stat(ref.artifact_id).media_type == "text/plain"
    with pytest.raises(ProviderUnavailableError):
        store.get(fingerprint_bytes(b"never stored"))


@pytest.fixture
def stalled_s3_endpoint() -> Iterator[str]:
    """Accept S3 requests without returning a response until teardown."""
    release = Event()

    class Handler(BaseHTTPRequestHandler):
        def stall(self) -> None:
            release.wait()
            self.close_connection = True

        do_PUT = stall  # noqa: N815 -- HTTP handler names are defined by the stdlib.
        do_GET = stall  # noqa: N815
        do_HEAD = stall  # noqa: N815

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


def test_stalled_s3_uses_filesystem_before_client_timeout(
    tmp_path: Path, stalled_s3_endpoint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even an inherited retry policy must not delay failover.
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "10")
    primary = S3ArtifactStore(
        ProviderDescriptor(
            id="stalled-minio",
            transport="s3",
            endpoint=stalled_s3_endpoint,
            capabilities=["artifact.store", "artifact.retrieve"],
            config={"access_key": "test", "secret_key": "test"},
        )
    )
    store = FailoverArtifactStore(primary, FilesystemArtifactStore(tmp_path / "backup"))

    started = monotonic()
    ref = store.put(b"stored despite stalled S3", media_type="text/plain", filename="test.txt")
    assert monotonic() - started < 15
    assert ref.storage_backend == "filesystem"

    # An API retrieval calls both get and stat, within its 30-second timeout.
    started = monotonic()
    assert store.get(ref.artifact_id) == b"stored despite stalled S3"
    assert store.stat(ref.artifact_id).filename == "test.txt"
    assert monotonic() - started < 25

    started = monotonic()
    assert store.exists(ref.artifact_id)
    assert monotonic() - started < 15
