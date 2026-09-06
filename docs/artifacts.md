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
