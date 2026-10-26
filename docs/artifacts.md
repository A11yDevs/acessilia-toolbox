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

When S3/MinIO is configured, it is the only artifact store used by the
application. A runtime outage returns `503 provider_unavailable`; uploads are
not redirected to a host-local filesystem. An upload is confirmed only after
the object store accepts the write. Replicas must use the same S3 endpoint and
bucket. Separate S3 deployments do not automatically share artifacts.

If configured S3 storage cannot be initialized (including unresolved environment
variables or a missing storage client), application startup fails rather than
silently selecting local storage. Runtime outages keep the API available but
reject artifact operations. Upload failures have an actionable public message
without provider exception details. The S3 client retains its 3-second connection
and 5-second read timeouts with one attempt per operation; these are socket
timeouts, not a total deadline for large transfers.

### Local development

Filesystem storage remains available when the provider configuration declares
**no S3 artifact store**. Copy `providers-config.yaml` to the gitignored
`var/providers-config.local.yaml` (create `var/` first), remove its `minio`
provider, and retain the existing `filesystem` provider. Set
`TOOLBOX_PROVIDERS_CONFIG=var/providers-config.local.yaml` when running locally.
The local root is `/tmp/acessilia-toolbox-artifacts` by default. A filesystem
write failure returns 503; local mode is intended for single-host development,
not independent replicas in production. If both S3 and filesystem are declared,
only S3 is selected, and the filesystem store is not initialized.

### Migrating existing fallback artifacts

The old release accepted filesystem writes during MinIO outages. Those objects
are **not** copied to S3 by this change. Before rolling out central-only storage:

1. Pause uploads/jobs and back up every existing fallback volume and its metadata.
2. Inventory the content-addressed objects on every host and upload them to the
   common MinIO bucket through the artifact API or S3 adapter. Preserve each
   object's media type and filename from its `.meta.json` file. Confirm that
   every accepted migration write reports `storage_backend: s3`. The S3 adapter
   derives the existing object key and ID from the same content hash.
3. Verify every returned artifact ID against its original ID, then download and
   compare hashes through each replica. Preserve old volumes until verified.
4. Switch all replicas to the new release. Test that an isolated MinIO outage
   rejects uploads with 503 and that a retry after recovery is readable through
   any replica. Do not stop production MinIO to perform this test.

Existing Compose filesystem volumes are retained for migration and explicit
local-only configurations; they are not runtime fallbacks for configured S3.
No NFS server or shared filesystem is required by the central-only policy.

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

Providers that produce files, such as `document.export.pdf_ua` and
`document.export.docx`, return them as
binary outputs. The executor writes each one to the artifact store and lists its
reference in `artifacts`; neither the response nor the cached result carries
the bytes. Before serving such a result from the cache, the executor checks that
the stored objects still exist and executes again when one is missing.

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
