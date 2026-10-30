"""Metadata remains complete and immutable on mounts without hard links."""

import errno
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from acessilia_toolbox.core.errors import ProviderUnavailableError
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.providers import storage
from acessilia_toolbox.providers.storage import FilesystemArtifactStore


@pytest.mark.parametrize("error_code", [errno.EPERM, errno.ENOTSUP])
def test_no_hardlink_concurrent_uploads_publish_complete_metadata(
    tmp_path, monkeypatch, error_code
):
    store = FilesystemArtifactStore(tmp_path)
    payload = b"same artifact"
    target = store._path(fingerprint_bytes(payload))
    metadata = target.with_suffix(target.suffix + storage.METADATA_SUFFIX)
    publishing, release, second_attempted = (threading.Event() for _ in range(3))
    attempts = []
    original_replace, original_open = Path.replace, Path.open

    def no_hardlinks(*args):
        attempts.append(args)
        if len(attempts) == 2:
            second_attempted.set()
        raise OSError(error_code, "hard links unsupported")

    def paused_replace(source, destination):
        if destination == metadata:
            publishing.set()
            assert release.wait(5), "metadata publication was not released"
        return original_replace(source, destination)

    def paused_open(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        # Also pause the old implementation after it creates an empty target,
        # so the assertion below detects publication before the JSON is ready.
        if path == metadata and mode == "xb":
            publishing.set()
            if not release.wait(5):
                stream.close()
                pytest.fail("metadata publication was not released")
        return stream

    monkeypatch.setattr(storage.os, "link", no_hardlinks)
    monkeypatch.setattr(Path, "replace", paused_replace)
    monkeypatch.setattr(Path, "open", paused_open)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(store.put, payload, filename="first.pdf")
        try:
            assert publishing.wait(5), "first upload did not reach publication"
            assert not metadata.exists(), "metadata became visible before it was complete"
            second = pool.submit(store.put, payload, filename="second.pdf")
            assert second_attempted.wait(5), "second upload did not reach metadata"
        finally:
            release.set()
        first_ref = first.result(timeout=5)
        assert second.result(timeout=5) == first_ref
    assert first_ref.filename == "first.pdf"
    assert store.stat(first_ref.artifact_id) == first_ref
    assert store.put(payload, filename="third.pdf") == first_ref
    assert not list(target.parent.glob(f".{metadata.name}.*"))


def test_failed_metadata_publish_releases_lock_and_can_be_retried(tmp_path, monkeypatch):
    store = FilesystemArtifactStore(tmp_path)
    payload = b"artifact"
    target = store._path(fingerprint_bytes(payload))
    metadata = target.with_suffix(target.suffix + storage.METADATA_SUFFIX)
    original_replace = Path.replace

    def no_hardlinks(*args):
        raise PermissionError("hard links unsupported")

    def failed_replace(source, destination):
        if destination == metadata:
            raise OSError("rename failed")
        return original_replace(source, destination)

    monkeypatch.setattr(storage.os, "link", no_hardlinks)
    monkeypatch.setattr(Path, "replace", failed_replace)
    with pytest.raises(ProviderUnavailableError):
        store.put(payload, filename="failed.pdf")
    assert not metadata.exists()
    assert not list(target.parent.glob(f".{metadata.name}.*"))
    monkeypatch.setattr(Path, "replace", original_replace)
    assert store.put(payload, filename="retry.pdf").filename == "retry.pdf"
