"""B08 characterization: fallback visibility depends on the storage topology.

Two independent REST apps use real S3 adapters against a fault-injection HTTP
server. Separate directories model separate hosts; a common directory models
the filesystem contract of a shared mount (not an actual distributed volume).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from acessilia_toolbox.api import auth
from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers import storage
from acessilia_toolbox.providers.storage import (
    FailoverArtifactStore,
    FilesystemArtifactStore,
    S3ArtifactStore,
)

pytestmark = pytest.mark.integration
PAYLOAD = b"B08: artifact uploaded while the central store is unavailable"


@dataclass
class PrimaryState:
    endpoint: str = ""
    unavailable: bool = True


@pytest.fixture
def primary() -> Iterator[PrimaryState]:
    state = PrimaryState()

    class Handler(BaseHTTPRequestHandler):
        def respond(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            code = "ServiceUnavailable" if state.unavailable else "NoSuchKey"
            body = f"<Error><Code>{code}</Code></Error>".encode()
            self.send_response(503 if state.unavailable else 404)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_PUT = respond  # noqa: N815 -- stdlib HTTP handler interface.
        do_GET = respond  # noqa: N815
        do_HEAD = respond  # noqa: N815

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.endpoint = f"http://127.0.0.1:{server.server_port}"
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def replica(primary: PrimaryState, root: Path) -> TestClient:
    descriptor = ProviderDescriptor(
        id="b08-primary",
        transport="s3",
        endpoint=primary.endpoint,
        capabilities=["artifact.store", "artifact.retrieve"],
        config={"access_key": "test", "secret_key": "test"},
    )
    store = FailoverArtifactStore(S3ArtifactStore(descriptor), FilesystemArtifactStore(root))
    return TestClient(create_app(providers=ProviderRegistry([descriptor]), store=store))


@pytest.mark.parametrize("shared", [False, True], ids=["isolated-disks", "shared-filesystem"])
def test_fallback_visibility_across_replicas(
    tmp_path: Path, primary: PrimaryState, monkeypatch: pytest.MonkeyPatch, shared: bool
) -> None:
    monkeypatch.setattr(auth, "TOOLBOX_API_KEY", "")
    root_a = tmp_path / "replica-a"
    root_b = root_a if shared else tmp_path / "replica-b"
    with replica(primary, root_a) as a, replica(primary, root_b) as b:
        uploaded = a.post("/v1/artifacts", files={"file": ("b08.txt", PAYLOAD, "text/plain")})
        assert uploaded.status_code == 200
        ref = uploaded.json()
        assert ref["storage_backend"] == "filesystem"
        assert ref["artifact_id"] == fingerprint_bytes(PAYLOAD)
        url = f"/v1/artifacts/{ref['artifact_id']}"
        assert a.get(url).content == PAYLOAD

        # While S3 is offline, B can only serve bytes on its own mounted filesystem.
        response = b.get(url)
        if shared:
            assert response.status_code == 200
            assert response.content == PAYLOAD
            assert response.headers["etag"] == ref["artifact_id"]
        else:
            assert response.status_code == 503
            assert response.json()["code"] == "provider_unavailable"

        # Recovery alone does not migrate files uploaded to the local fallback.
        primary.unavailable = False
        after_recovery = b.get(url)
        assert after_recovery.status_code == (200 if shared else 404)
        if shared:
            assert after_recovery.content == PAYLOAD
        else:
            assert after_recovery.json()["code"] == "artifact_not_found"

    # A newly created app still sees A's persistent filesystem, even without S3.
    primary.unavailable = True
    with replica(primary, root_a) as restarted_a:
        response = restarted_a.get(url)
        assert response.status_code == 200
        assert response.content == PAYLOAD


@pytest.mark.parametrize("fail_on_write", [1, 2], ids=["payload", "metadata"])
def test_upload_is_not_accepted_when_fallback_cannot_persist(
    tmp_path: Path,
    primary: PrimaryState,
    monkeypatch: pytest.MonkeyPatch,
    fail_on_write: int,
) -> None:
    monkeypatch.setattr(auth, "TOOLBOX_API_KEY", "")
    original_write = storage._atomic_write
    writes = 0

    def failing_write(target: Path, payload: bytes) -> None:
        nonlocal writes
        writes += 1
        if writes == fail_on_write:
            raise OSError("private mount /srv/artifacts: no space left")
        original_write(target, payload)

    monkeypatch.setattr(storage, "_atomic_write", failing_write)
    with replica(primary, tmp_path / "shared") as client:
        response = client.post("/v1/artifacts", files={"file": ("b08.txt", PAYLOAD, "text/plain")})
        assert response.status_code == 503
        body = response.json()
        assert body["code"] == "provider_unavailable"
        assert "artifact_id" not in body
        assert "/srv/artifacts" not in response.text

        # The same request can succeed after storage recovers, including when
        # the previous attempt wrote the payload but failed on its metadata.
        monkeypatch.setattr(storage, "_atomic_write", original_write)
        retry = client.post("/v1/artifacts", files={"file": ("b08.txt", PAYLOAD, "text/plain")})
        assert retry.status_code == 200
        artifact_id = retry.json()["artifact_id"]
        assert artifact_id == fingerprint_bytes(PAYLOAD)
        assert client.get(f"/v1/artifacts/{artifact_id}").content == PAYLOAD
