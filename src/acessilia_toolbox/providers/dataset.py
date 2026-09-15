"""Dataset provider protocol and adapter bridge.

A `DatasetProvider` knows how to talk to one remote dataset source (GitHub,
HuggingFace, …).  The `DatasetAdapter` wraps it so it satisfies the
`ProviderAdapter` protocol that the executor expects.

Mirroring
---------
When an ``ArtifactStore`` is provided and the provider config has
``mirror: true``, dataset artifacts fetched via ``get_artifact`` are
persisted in the store for subsequent fast retrieval.  Metadata
(list/describe/splits) is cached in ``ExecutionCache`` when available.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from acessilia_toolbox.core.artifact import ArtifactRef, ArtifactStore, ExecutionCache, NullCache
from acessilia_toolbox.core.dataset import DatasetInfo, DatasetItem, ItemSummary, SplitInfo
from acessilia_toolbox.core.errors import DatasetProviderError, ToolboxError
from acessilia_toolbox.core.fingerprint import fingerprint_bytes
from acessilia_toolbox.core.normalization.extraction import ExtractionResult
from acessilia_toolbox.core.provider import ProviderDescriptor, ProviderHealth

# Cache key prefixes
_META_CACHE_PREFIX = "dataset:meta:"
_ARTIFACT_CACHE_PREFIX = "dataset:artifact:"
_META_TTL = 300  # 5 minutes for metadata
_ARTIFACT_TTL = 86400 * 7  # 7 days for mirrored artifact references


@runtime_checkable
class DatasetProvider(Protocol):
    """Protocol for dataset access providers.

    Each method maps to one capability operation.  Implementations fetch from
    the remote source and return the normalised model objects.
    """

    descriptor: ProviderDescriptor

    def list_datasets(self) -> Sequence[DatasetInfo]: ...

    def describe(self, dataset_id: str, revision: str | None = None) -> DatasetInfo: ...

    def list_splits(
        self, dataset_id: str, revision: str | None = None
    ) -> Sequence[SplitInfo]: ...

    def list_items(
        self,
        dataset_id: str,
        split: str,
        revision: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[ItemSummary]: ...

    def get_item(
        self,
        dataset_id: str,
        item_id: str,
        split: str,
        revision: str | None = None,
    ) -> DatasetItem: ...

    def get_artifact(
        self,
        dataset_id: str,
        artifact_path: str,
        revision: str | None = None,
    ) -> tuple[bytes, str]: ...

    def sample(
        self,
        dataset_id: str,
        split: str,
        n: int = 5,
        revision: str | None = None,
    ) -> Sequence[DatasetItem]: ...


class DatasetAdapter:
    """Wraps a DatasetProvider to conform to ProviderAdapter.

    The executor's ``if isinstance(extraction.document, dict)`` branch handles
    the plain dict returned here - no processing-manifest builder needed.

    When an ``ArtifactStore`` is supplied and the provider config has
    ``mirror: true``, artifacts are mirrored to the store on first fetch.
    """

    def __init__(
        self,
        descriptor: ProviderDescriptor,
        provider: DatasetProvider,
        *,
        store: ArtifactStore | None = None,
        cache: ExecutionCache | None = None,
    ) -> None:
        self.descriptor = descriptor
        self._provider = provider
        self._store = store
        self._cache = cache or NullCache()
        self._mirror = bool(descriptor.config.get("mirror", False))

    # ------------------------------------------------------------------
    # ProviderAdapter protocol
    # ------------------------------------------------------------------

    def execute(
        self,
        capability_id: str,
        payload: bytes,
        *,
        filename: str = "",
        media_type: str = "",
        parameters: Mapping[str, Any] | None = None,
    ) -> ExtractionResult:
        params = dict(parameters or {})
        dataset_id = params.pop("dataset_id", "")
        revision = params.pop("revision", None)
        split = params.pop("split", "")
        item_id = params.pop("item_id", "")
        artifact_path = params.pop("artifact_path", "")
        limit = int(params.pop("limit", 100))
        offset = int(params.pop("offset", 0))
        n = int(params.pop("n", 5))

        started = datetime.now(UTC)

        try:
            document = self._dispatch(
                capability_id,
                dataset_id=dataset_id,
                revision=revision,
                split=split,
                item_id=item_id,
                artifact_path=artifact_path,
                limit=limit,
                offset=offset,
                n=n,
            )
        except ToolboxError:
            raise
        except Exception as exc:
            raise DatasetProviderError(
                f"{self.descriptor.id} failed on {capability_id}: {exc}",
                capability=capability_id,
                provider=self.descriptor.id,
            ) from exc

        completed = datetime.now(UTC)
        duration = int((completed - started).total_seconds() * 1000)

        return ExtractionResult(
            document=document,
            backend=self.descriptor.id,
            started_at=started,
            completed_at=completed,
            duration_ms=duration,
            version=self.descriptor.version,
        )

    def versions(self) -> dict[str, str]:
        return {"provider": self.descriptor.version}

    def health(self) -> ProviderHealth:
        try:
            self._provider.list_datasets()
            return ProviderHealth(
                provider=self.descriptor.id,
                healthy=True,
                version=self.descriptor.version,
                checked_at=datetime.now(UTC),
            )
        except Exception as exc:
            return ProviderHealth(
                provider=self.descriptor.id,
                healthy=False,
                version=self.descriptor.version,
                detail=str(exc),
                checked_at=datetime.now(UTC),
            )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, capability_id: str, **kw: Any) -> Any:
        """Route a capability ID to the matching provider method."""
        mapping: dict[str, str] = {
            "dataset.list": "list_datasets",
            "dataset.describe": "describe",
            "dataset.list_splits": "list_splits",
            "dataset.list_items": "list_items",
            "dataset.get_item": "get_item",
            "dataset.get_artifact": "get_artifact",
            "dataset.sample": "sample",
            "dataset.sync": "sync",
        }
        method_name = mapping.get(capability_id)
        if method_name is None:
            raise DatasetProviderError(
                f"unknown dataset capability {capability_id}",
                capability=capability_id,
            )

        method = getattr(self._provider, method_name, None)
        if method_name == "sync":
            # sync is implemented on the adapter itself (needs store/mirror),
            # not on the underlying provider object.
            method = self._sync
        if method is None:
            raise DatasetProviderError(
                f"provider {self.descriptor.id} does not implement {capability_id}",
                capability=capability_id,
            )

        # Pre-compute cache key for metadata operations.
        cache_key = self._meta_cache_key(method_name, kw)

        # Metadata operations: try cache first.
        if method_name in ("list_datasets", "describe", "list_splits", "list_items"):
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached["data"]

        # Build the call signature from kw.
        sig = {
            "list_datasets": lambda: self._list_datasets_cached(cache_key),
            "describe": lambda: self._describe_cached(
                kw["dataset_id"], kw.get("revision"), cache_key
            ),
            "list_splits": lambda: self._list_splits_cached(
                kw["dataset_id"], kw.get("revision"), cache_key
            ),
            "list_items": lambda: self._list_items_cached(
                kw["dataset_id"], kw["split"], kw.get("revision"),
                kw["limit"], kw["offset"], cache_key
            ),
            "get_item": lambda: self._provider.get_item(
                kw["dataset_id"], kw["item_id"], kw["split"], kw.get("revision")
            ),
            "get_artifact": lambda: self._get_artifact_maybe_mirrored(
                kw["dataset_id"], kw["artifact_path"], kw.get("revision")
            ),
            "sample": lambda: self._provider.sample(
                kw["dataset_id"], kw["split"], kw["n"], kw.get("revision")
            ),
            "sync": lambda: self._sync(
                kw["dataset_id"], kw.get("revision"), kw.get("split")
            ),
        }

        result = sig[method_name]()

        # Serialise to plain dict so the executor's dict branch handles it.
        if isinstance(result, tuple) and len(result) == 2:
            # get_artifact returns (bytes, media_type) - wrap in a dict.
            return {"payload": result[0].hex(), "media_type": result[1]}
        if isinstance(result, list):
            return [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in result
            ]
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        return result  # already a dict (sync report, cached data)

    # ------------------------------------------------------------------
    # Cached metadata helpers
    # ------------------------------------------------------------------

    def _list_datasets_cached(self, cache_key: str) -> Sequence[DatasetInfo]:
        cached = self._cache.get(cache_key)
        if cached is not None:
            return [DatasetInfo.model_validate(d) for d in cached["data"]]
        result = self._provider.list_datasets()
        self._cache.put(cache_key, {"data": [d.model_dump(mode="json") for d in result]})
        return result

    def _describe_cached(
        self, dataset_id: str, revision: str | None, cache_key: str
    ) -> DatasetInfo:
        cached = self._cache.get(cache_key)
        if cached is not None:
            return DatasetInfo.model_validate(cached["data"])
        result = self._provider.describe(dataset_id, revision)
        self._cache.put(cache_key, {"data": result.model_dump(mode="json")})
        return result

    def _list_splits_cached(
        self, dataset_id: str, revision: str | None, cache_key: str
    ) -> Sequence[SplitInfo]:
        cached = self._cache.get(cache_key)
        if cached is not None:
            return [SplitInfo.model_validate(s) for s in cached["data"]]
        result = self._provider.list_splits(dataset_id, revision)
        self._cache.put(cache_key, {"data": [s.model_dump(mode="json") for s in result]})
        return result

    def _list_items_cached(
        self,
        dataset_id: str,
        split: str,
        revision: str | None,
        limit: int,
        offset: int,
        cache_key: str,
    ) -> Sequence[ItemSummary]:
        cached = self._cache.get(cache_key)
        if cached is not None:
            return [ItemSummary.model_validate(i) for i in cached["data"]]
        result = self._provider.list_items(dataset_id, split, revision, limit, offset)
        # Don't cache empty listings — they usually indicate a transient
        # upstream failure (e.g. HF rate limit) and would poison the cache.
        if result:
            self._cache.put(cache_key, {"data": [i.model_dump(mode="json") for i in result]})
        return result

    # ------------------------------------------------------------------
    # Mirroring
    # ------------------------------------------------------------------

    def _get_artifact_maybe_mirrored(
        self,
        dataset_id: str,
        artifact_path: str,
        revision: str | None = None,
    ) -> tuple[bytes, str]:
        """Fetch an artifact, optionally mirroring it to the store."""
        if not self._mirror or self._store is None:
            return self._provider.get_artifact(dataset_id, artifact_path, revision)

        # Check store first (content-addressed lookup).
        store_key = self._store_key(dataset_id, artifact_path)
        if self._store.exists(store_key):
            payload = self._store.get(store_key)
            ref = self._store.stat(store_key)
            return payload, ref.media_type

        # Fetch from remote.
        payload, media_type = self._provider.get_artifact(dataset_id, artifact_path, revision)

        # Store with a deterministic key derived from the dataset path.
        self._store.put(
            payload,
            media_type=media_type,
            filename=artifact_path.rsplit("/", 1)[-1],
        )
        # Also store a reference under the dataset-scoped key so we can find it.
        ref = ArtifactRef.of(
            payload,
            media_type=media_type,
            filename=artifact_path.rsplit("/", 1)[-1],
        )
        self._cache.put(
            _ARTIFACT_CACHE_PREFIX + store_key,
            ref.model_dump(mode="json"),
        )

        return payload, media_type

    def _sync(
        self,
        dataset_id: str,
        revision: str | None = None,
        split: str | None = None,
    ) -> list[dict[str, Any]]:
        """Re-mirror all artifacts for a dataset (optionally one split)."""
        if not self._mirror or self._store is None:
            return [{"status": "skipped", "reason": "mirroring is disabled"}]

        splits = [split] if split else list(
            getattr(self._provider.describe(dataset_id, revision), "splits", [])
        )

        synced: list[dict[str, Any]] = []
        for sp in splits:
            items = self._provider.list_items(dataset_id, sp, revision, limit=10000, offset=0)
            for summary in items:
                try:
                    item = self._provider.get_item(dataset_id, summary.id, sp, revision)
                except Exception as exc:
                    # HF rate limits / transient 404s must not abort the whole
                    # sync; record and continue.
                    synced.append({
                        "item": summary.id,
                        "split": sp,
                        "status": "skipped",
                        "reason": str(exc),
                    })
                    continue
                for art in item.artifacts:
                    try:
                        self._get_artifact_maybe_mirrored(dataset_id, art.path, revision)
                        synced.append({
                            "item": summary.id,
                            "split": sp,
                            "artifact": art.path,
                            "status": "mirrored",
                        })
                    except Exception as exc:
                        synced.append({
                            "item": summary.id,
                            "split": sp,
                            "artifact": art.path,
                            "status": "skipped",
                            "reason": str(exc),
                        })
                for ann in item.annotations:
                    try:
                        self._get_artifact_maybe_mirrored(dataset_id, ann.path, revision)
                        synced.append({
                            "item": summary.id,
                            "split": sp,
                            "artifact": ann.path,
                            "status": "mirrored",
                        })
                    except Exception as exc:
                        synced.append({
                            "item": summary.id,
                            "split": sp,
                            "artifact": ann.path,
                            "status": "skipped",
                            "reason": str(exc),
                        })

        return synced

    def _store_key(self, dataset_id: str, artifact_path: str) -> str:
        """Deterministic store key for a dataset artifact."""
        raw = f"datasets/{dataset_id}/{artifact_path}".encode()
        return fingerprint_bytes(raw)

    def _meta_cache_key(self, operation: str, kw: dict[str, Any]) -> str:
        """Cache key for metadata operations."""
        rev = kw.get("revision") or "latest"
        parts = [
            _META_CACHE_PREFIX,
            self.descriptor.id,
            ":",
            kw.get("dataset_id", ""),
            ":",
            rev,
            ":",
            operation,
        ]
        # Pagination params must be part of the key, otherwise a page fetched
        # with limit=5 would be served for any other limit/offset combination.
        if operation == "list_items":
            parts += [":", str(kw.get("limit", "")), ":", str(kw.get("offset", ""))]
        return "".join(parts)


__all__ = [
    "DatasetAdapter",
    "DatasetProvider",
]
