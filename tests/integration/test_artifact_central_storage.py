"""B08: rejected uploads cannot become artifacts visible only on one replica."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from acessilia_toolbox.api import auth
from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderRegistry
from acessilia_toolbox.providers.storage import FilesystemArtifactStore, S3ArtifactStore

pytestmark = pytest.mark.integration
PAYLOAD = b"B08 central artifact"


@dataclass
class CentralStore:
    endpoint: str = ""
    unavailable: bool = True
    objects: dict[str, tuple[bytes, str, str]] = field(default_factory=dict)


@pytest.fixture
def central() -> Iterator[CentralStore]:
    state = CentralStore()

    class Handler(BaseHTTPRequestHandler):
        def respond(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if state.unavailable:
                self.send_response(503)
                body = b"<Error><Code>ServiceUnavailable</Code></Error>"
            elif self.command == "PUT":
                state.objects[self.path] = (
                    body,
                    self.headers.get("Content-Type", "application/octet-stream"),
                    self.headers.get("x-amz-meta-filename-utf8", ""),
                )
                self.send_response(200)
                body = b""
            elif self.path not in state.objects:
                self.send_response(404)
                body = b"<Error><Code>NoSuchKey</Code></Error>"
            else:
                body, media_type, filename = state.objects[self.path]
                self.send_response(200)
                self.send_header("Content-Type", media_type)
                self.send_header("x-amz-meta-filename-utf8", filename)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_PUT = respond  # noqa: N815 -- stdlib HTTP interface.
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


def replica(central: CentralStore, local_root: Path) -> TestClient:
    providers = ProviderRegistry(
        [
            ProviderDescriptor(
                id="minio",
                transport="s3",
                endpoint=central.endpoint,
                capabilities=["artifact.store", "artifact.retrieve"],
                config={"access_key": "test", "secret_key": "test"},
            ),
            ProviderDescriptor(
                id="filesystem",
                transport="in_process",
                capabilities=["artifact.store", "artifact.retrieve"],
                config={"root": str(local_root)},
            ),
        ]
    )
    app = create_app(providers=providers)
    assert isinstance(app.state.store, S3ArtifactStore)
    return TestClient(app)


def test_outage_rejects_upload_then_retry_is_available_on_every_replica(
    tmp_path: Path, central: CentralStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "TOOLBOX_API_KEY", "")
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    with replica(central, root_a) as a, replica(central, root_b) as b:
        for client in (a, b):
            response = client.post(
                "/v1/artifacts", files={"file": ("b08.txt", PAYLOAD, "text/plain")}
            )
            assert response.status_code == 503
            assert response.json()["code"] == "provider_unavailable"
            assert "try again" in response.json()["message"]
            assert "artifact_id" not in response.json()
        assert not central.objects
        assert not root_a.exists() and not root_b.exists()

        central.unavailable = False
        response = a.post("/v1/artifacts", files={"file": ("b08.txt", PAYLOAD, "text/plain")})
        assert response.status_code == 200
        ref = response.json()
        assert ref["storage_backend"] == "s3"
        assert ref["artifact_id"] == fingerprint_bytes(PAYLOAD)
        url = f"/v1/artifacts/{ref['artifact_id']}"
        downloaded = b.get(url)
        assert downloaded.status_code == 200
        assert downloaded.content == PAYLOAD
        assert downloaded.headers["etag"] == ref["artifact_id"]
        assert b.get(url + "/metadata").json()["filename"] == "b08.txt"

        central.unavailable = True
        assert a.get(url).status_code == 503
        assert b.get(url).status_code == 503
        central.unavailable = False
        assert b.get(url).content == PAYLOAD
        assert not root_a.exists() and not root_b.exists()

    with replica(central, root_a) as replacement:
        assert replacement.get(url).content == PAYLOAD


def test_legacy_local_artifact_is_not_used_as_a_central_fallback(
    tmp_path: Path, central: CentralStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "TOOLBOX_API_KEY", "")
    root = tmp_path / "legacy"
    legacy = FilesystemArtifactStore(root)
    ref = legacy.put(PAYLOAD, filename="legacy.txt")
    url = f"/v1/artifacts/{ref.artifact_id}"
    with replica(central, root) as client:
        assert client.get(url).status_code == 503
        central.unavailable = False
        assert client.get(url).status_code == 404
    # Removing automatic reads must not delete historical files needed for migration.
    assert legacy.get(ref.artifact_id) == PAYLOAD
