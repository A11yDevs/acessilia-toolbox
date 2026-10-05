# Artifacts, Storage, and Cache

## Principle

The Toolbox is stateless even when it accesses stateful providers.

MinIO, S3, filesystem storage, Valkey, or Redis may retain data. The
Toolbox only mediates explicitly requested operations.

## Artifact references

Prefer references over repeatedly transferring large blobs:

``` json
{
  "artifact_id": "sha256:8a3f...",
  "uri": "s3://acessilia/objects/8a/3f/...",
  "media_type": "application/pdf",
  "size": 2849321
}
```

Public responses should avoid exposing storage credentials. Presigned
URLs may be used when appropriate.

## Content-addressable storage

Where practical, derive immutable artifact identity from content:

``` text
SHA-256(content) -> artifact_id
```

Benefits:

- deduplication;
- cache keys;
- provenance;
- integrity checking;
- reproducibility.

Logical metadata and user-facing filenames should be separate from
immutable content identity.

## MinIO

MinIO is a provider for artifact capabilities, not internal Toolbox
state.

Suggested capabilities:

``` text
artifact.store
artifact.retrieve
artifact.exists
artifact.delete
artifact.presign
```

The same contract could later be implemented by S3, Ceph, or local
filesystem storage.

If MinIO is unavailable during a write, the Toolbox stores the artifact
on the filesystem. Reads check both stores. If MinIO is unavailable and
the artifact is absent from the filesystem, report provider unavailability.
Artifacts stored on the filesystem are not copied back to MinIO automatically.

The S3 client uses a 3-second connection timeout, a 5-second read timeout,
and one attempt per operation, so an unresponsive primary can yield to the
filesystem before the client's 30-second request timeout. These are socket
timeouts, not a total deadline for transferring large artifacts. If the
filesystem cannot be initialized, the Toolbox logs a warning and continues
with the available S3 store.

The filesystem root is `/tmp/acessilia-toolbox-artifacts`. Docker Compose
mounts a named volume there to retain artifacts across container recreation.
Without a mount, files in `/tmp` are temporary. Replicas on different hosts
need the same persistent shared filesystem at this path. Set
`ARTIFACT_FALLBACK_SOURCE` to the host path of that shared mount in each
Compose deployment; the default named volume only covers one host.

## Cache

Cache should be external and replaceable.

A deterministic execution cache key should include at least:

``` text
input fingerprint
+ capability ID/version
+ provider ID/version
+ normalized parameters
+ relevant model/configuration versions
```

Large cached results may be stored in object storage, with a fast cache
containing only metadata or artifact references.

## Provenance

Execution metadata should include:

- input artifact fingerprints;
- capability and version;
- provider and version;
- normalized parameter fingerprint;
- timestamps/duration;
- output artifact fingerprints;
- relevant model/configuration versions.

Updating a provider must not silently reuse incompatible cached results.
