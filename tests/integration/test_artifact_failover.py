"""Central storage outages must be reported instead of accepting local writes."""

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
    S3ArtifactStore,
)

pytestmark = pytest.mark.integration


def test_unreachable_s3_endpoint_rejects_upload(tmp_path: Path) -> None:
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
    with pytest.raises(ProviderUnavailableError, match="could not be stored"):
        primary.put(b"rejected during outage", media_type="text/plain")
    with pytest.raises(ProviderUnavailableError):
        primary.get(fingerprint_bytes(b"never stored"))
    assert list(tmp_path.iterdir()) == []


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


def test_stalled_s3_reports_failure_before_client_timeout(
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
    started = monotonic()
    with pytest.raises(ProviderUnavailableError):
        primary.put(b"rejected stalled S3", media_type="text/plain", filename="test.txt")
    assert monotonic() - started < 15
    assert list(tmp_path.iterdir()) == []
