import pytest
from tests.unit.test_artifact_storage import FakeS3Client

from acessilia_toolbox.core.errors import ProviderUnavailableError
from acessilia_toolbox.providers.storage import S3ArtifactStore


class ConflictError(Exception):
    def __init__(self, code):
        self.response = {'Error': {'Code': code}}


@pytest.mark.parametrize('codes, expected_calls, success', [
    (['ConditionalRequestConflict'], 2, True),
    (['409', '409', '409'], 3, False),
    (['AccessDenied'], 1, False),
])
def test_conditional_write_retries_only_conflicts(monkeypatch, codes, expected_calls, success):
    class Client(FakeS3Client):
        calls = 0
        def put_object(self, **kwargs):
            self.calls += 1
            if self.calls <= len(codes):
                raise ConflictError(codes[self.calls - 1])
            assert kwargs['IfNoneMatch'] == '*'
            return super().put_object(**kwargs)

    monkeypatch.setattr('acessilia_toolbox.providers.storage.time.sleep', lambda _: None)
    store = object.__new__(S3ArtifactStore)
    store.bucket = 'test'
    store._client = Client()
    if success:
        assert store.put(b'data').size == 4
    else:
        with pytest.raises(ProviderUnavailableError):
            store.put(b'data')
    assert store._client.calls == expected_calls


def test_conflict_then_precondition_returns_winner(monkeypatch):
    class Client(FakeS3Client):
        calls = 0
        def put_object(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                super().put_object(**{**kwargs, 'Metadata': {'filename-utf8': 'first.pdf'}})
                raise ConflictError('ConditionalRequestConflict')
            raise ConflictError('PreconditionFailed')

    monkeypatch.setattr('acessilia_toolbox.providers.storage.time.sleep', lambda _: None)
    store = object.__new__(S3ArtifactStore)
    store.bucket = 'test'
    store._client = Client()
    assert store.put(b'data', filename='second.pdf').filename == 'first.pdf'
    assert store._client.calls == 2
