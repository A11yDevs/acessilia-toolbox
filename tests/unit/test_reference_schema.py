"""``artifact/reference@1``: the schema, its model and the only adapter that builds it (#145).

The schema publishes what ADR-0015 (in the Acessilia core) asks of a
reference at version 1. Draft-07 cannot state that ``digest`` equals
``artifact_id``, so ``reference_of``, the only producer, guarantees it; these
tests check it there, and check that what the upload route sends over HTTP
validates against the published file.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from acessilia_toolbox.api.app import create_app
from acessilia_toolbox.core.artifact import ArtifactRef, ArtifactReference, reference_of
from acessilia_toolbox.core.capability import CapabilityRegistry
from acessilia_toolbox.core.errors import InvalidInputError
from acessilia_toolbox.core.provider import ProviderRegistry
from acessilia_toolbox.providers.storage import FilesystemArtifactStore

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas" / "reference@1.json").read_text(encoding="utf-8"))
HEX = "ab" * 32
CANONICAL = f"sha256:{HEX}"


def _record(**changes) -> ArtifactRef:
    values = {
        "artifact_id": CANONICAL,
        "media_type": "application/pdf",
        "size": 10,
        "uri": "file:///tmp/x",
        "storage_backend": "filesystem",
        "filename": "doc.pdf",
    }
    return ArtifactRef(**{**values, **changes})


def _validate(document: dict) -> None:
    jsonschema.Draft7Validator(SCHEMA).validate(document)


def test_the_schema_is_a_valid_draft_7_schema() -> None:
    jsonschema.Draft7Validator.check_schema(SCHEMA)
    assert SCHEMA["$id"] == "urn:a11y-devs:schema:reference:1.0.0"
    assert SCHEMA["additionalProperties"] is False
    assert sorted(SCHEMA["required"]) == ["artifact_id", "digest", "media_type", "size", "store"]


def test_the_model_and_the_schema_name_the_same_fields() -> None:
    assert set(ArtifactReference.model_fields) == set(SCHEMA["properties"])


def test_a_stored_record_becomes_a_valid_reference() -> None:
    reference = reference_of(_record())

    document = reference.model_dump(mode="json")
    _validate(document)
    assert document == {
        "artifact_id": CANONICAL,
        "digest": CANONICAL,
        "media_type": "application/pdf",
        "size": 10,
        "store": "filesystem",
    }


def test_an_id_without_its_prefix_is_published_in_canonical_form() -> None:
    """S3 and filesystem ``stat`` echo an id given without ``sha256:``."""
    reference = reference_of(_record(artifact_id=HEX))

    assert reference.artifact_id == reference.digest == CANONICAL
    _validate(reference.model_dump(mode="json"))


@pytest.mark.parametrize("artifact_id", ["sha256:xyz", "md5:" + "ab" * 16, "AB" * 32, ""])
def test_a_malformed_id_is_refused(artifact_id: str) -> None:
    with pytest.raises(InvalidInputError):
        reference_of(_record(artifact_id=artifact_id))


def test_no_storage_location_is_published_as_null() -> None:
    """A result never stored, or a cache hit served inline while the store is down."""
    reference = reference_of(_record(storage_backend=None, uri=None))

    assert reference.store is None
    _validate(reference.model_dump(mode="json"))


def test_the_failover_wrapper_is_never_published_as_the_store() -> None:
    with pytest.raises(ValueError, match="never the failover wrapper"):
        reference_of(_record(storage_backend="failover"))


@pytest.mark.parametrize("store", ["s3", "filesystem", "memory"])
def test_the_store_is_an_open_string(store: str) -> None:
    _validate(reference_of(_record(storage_backend=store)).model_dump(mode="json"))


def test_the_schema_alone_accepts_a_digest_mismatch() -> None:
    """Draft-07 cannot say ``digest == artifact_id``: two valid, different digests pass.

    This documents the limit the issue records; the invariant is the adapter's
    (next test), and the SDK checks it again when it reads a reference.
    """
    produced = reference_of(_record()).model_dump(mode="json")
    tampered = {**produced, "digest": "sha256:" + "cd" * 32}

    _validate(tampered)  # no ValidationError


@pytest.mark.parametrize("given", [HEX, CANONICAL, "0" * 64, "sha256:" + "f" * 64])
def test_the_adapter_always_publishes_the_digest_equal_to_the_id(given: str) -> None:
    reference = reference_of(_record(artifact_id=given))

    assert reference.digest == reference.artifact_id
    assert reference.artifact_id.startswith("sha256:")
    assert reference.artifact_id.removeprefix("sha256:") == given.removeprefix("sha256:")


def test_the_upload_route_sends_a_reference_that_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOOLBOX_ALLOW_UNAUTHENTICATED", "1")
    store = FilesystemArtifactStore(tmp_path / "objects")
    client = TestClient(create_app(CapabilityRegistry([]), ProviderRegistry([]), store=store))

    response = client.post(
        "/v1/artifacts", files={"file": ("doc.pdf", b"%PDF-1.4 bytes", "application/pdf")}
    )

    assert response.status_code == 200
    document = response.json()
    _validate(document)
    assert document["digest"] == document["artifact_id"]
    assert document["store"] == "filesystem"
    assert document["media_type"] == "application/pdf"
    # The stored record keeps the fields the reference leaves out.
    record = client.get(f"/v1/artifacts/{document['artifact_id']}/metadata").json()
    assert record["filename"] == "doc.pdf"
    assert record["storage_backend"] == "filesystem"
