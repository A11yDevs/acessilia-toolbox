"""Artifact storage falls back when the S3 endpoint cannot be reached."""

from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from botocore.config import Config

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
    primary._client = boto3.client(
        "s3",
        endpoint_url=descriptor.endpoint,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(connect_timeout=1, read_timeout=1, retries={"max_attempts": 0}),
    )
    store = FailoverArtifactStore(
        primary, FilesystemArtifactStore(tmp_path / "backup")
    )

    ref = store.put(b"stored during outage", media_type="text/plain")

    assert ref.storage_backend == "filesystem"
    assert store.get(ref.artifact_id) == b"stored during outage"
    assert store.stat(ref.artifact_id).media_type == "text/plain"
    with pytest.raises(ProviderUnavailableError):
        store.get(fingerprint_bytes(b"never stored"))
