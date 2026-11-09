"""artifact/reference@1 keeps existing records and stores working (#145).

The upload route now answers with the published reference, while the stored
record keeps its own format. These cases come from the review of #151: a
record written before the change, and an upload served by the fallback store.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import jsonschema
import pytest
from fastapi.testclient import TestClient

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.artifact import ArtifactRef, NullCache
from acessilia_toolbox.core.errors import ProviderUnavailableError
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.provider import ProviderRegistry
from acessilia_toolbox.providers.storage import (
    METADATA_SUFFIX,
    FailoverArtifactStore,
    FilesystemArtifactStore,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas" / "reference@1.json").read_text(encoding="utf-8"))
PAYLOAD = b"%PDF-1.4 stored before reference@1"


@pytest.fixture(autouse=True)
def _unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")


def _seed_legacy_record(store: FilesystemArtifactStore) -> tuple[str, bytes]:
    """Write a blob and its .meta.json the way the stores did before #145."""
    artifact_id = fingerprint_bytes(PAYLOAD)
    target = store._path(artifact_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(PAYLOAD)
    record = ArtifactRef(
        artifact_id=artifact_id,
        media_type="application/pdf",
        size=len(PAYLOAD),
        uri=target.as_uri(),
        storage_backend="filesystem",
        filename="relatório antigo.pdf",
    ).model_dump_json().encode("utf-8")
    target.with_suffix(target.suffix + METADATA_SUFFIX).write_bytes(record)
    return artifact_id, record


def test_a_record_written_before_reference_at_1_is_still_served(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path / "objects")
    artifact_id, record = _seed_legacy_record(store)
    blob = store._path(artifact_id)
    meta_path = blob.with_suffix(blob.suffix + METADATA_SUFFIX)

    app = create_app(providers=ProviderRegistry(), store=store, cache=NullCache())
    with TestClient(app) as client:
        metadata = client.get(f"/v1/artifacts/{artifact_id}/metadata")
        download = client.get(f"/v1/artifacts/{artifact_id}")
        upload = client.post(
            "/v1/artifacts", files={"file": ("other-name.pdf", PAYLOAD, "application/pdf")}
        )

    # The metadata route still serves the stored record, field for field.
    assert metadata.status_code == 200
    assert metadata.json() == json.loads(record)
    # The download still names the file it was stored under.
    assert download.status_code == 200
    assert download.content == PAYLOAD
    assert "filename*=UTF-8''relat%C3%B3rio%20antigo.pdf" in download.headers["content-disposition"]
    # Uploading the same bytes again answers with the published reference ...
    assert upload.status_code == 200
    jsonschema.Draft7Validator(SCHEMA).validate(upload.json())
    assert upload.json()["artifact_id"] == upload.json()["digest"] == artifact_id
    # ... and leaves the existing record untouched.
    assert meta_path.read_bytes() == record


def test_an_upload_served_by_the_fallback_names_the_fallback_store(tmp_path: Path) -> None:
    """The failover wrapper returns the reference of the store that wrote it."""
    primary = Mock()
    for method in (primary.put, primary.put_stream, primary.get, primary.stat, primary.exists):
        method.side_effect = ProviderUnavailableError("S3 offline")
    store = FailoverArtifactStore(
        primary, FilesystemArtifactStore(tmp_path / "fallback"), fallback_mode="local"
    )

    app = create_app(providers=ProviderRegistry(), store=store, cache=NullCache())
    with TestClient(app) as client:
        response = client.post(
            "/v1/artifacts", files={"file": ("doc.pdf", PAYLOAD, "application/pdf")}
        )

    assert response.status_code == 200
    jsonschema.Draft7Validator(SCHEMA).validate(response.json())
    assert response.json()["store"] == "filesystem"
