# Cache and artifact resilience

This document describes three fixes developed from `release/0.2.0` at
`8f33ea6`. The changes address cached artifact references, image orientation
cache identity, and storage behavior during S3 outages.

## Cached results can reference a missing JSON artifact

### Problem

`CapabilityExecutor._outputs_available()` checked only artifacts after the
first entry. The first entry identifies the JSON document, while subsequent
entries identify binary outputs. The JSON is also included inline in the
response, but its stored reference can still be used for download.

If retention or another process removed the stored JSON without removing the
execution cache entry, the availability check could accept an incomplete result.

### Symptoms

A request returns a successful cache hit and an inline document. Downloading
the first artifact by its ID then returns a missing-artifact error. Binary
downloads can continue working, making the response appear partially healthy.

For example, deleting only the JSON associated with a cached PDF/UA export
previously left the cache hit valid, even though that JSON could no longer be
downloaded.

### Solution implemented

When an artifact store is configured, the executor checks every artifact
reference, including the JSON. If any object is missing, the cached result is
not returned: execution runs again, persists its outputs and replaces the
cache entry. This deliberately uses the existing execution path rather than
introducing a separate JSON reconstruction mechanism.

Without a store, a single inline JSON reference remains valid if it declares
neither a storage backend nor a URI. Stored references and binary outputs
cannot be accepted through this inline-only exception.

Regression tests remove only the JSON or only the PDF, confirm reexecution,
download every newly returned artifact and verify its content hash. Additional
coverage checks JSON-only results, subsequent cache hits and inline-only use.

The existence check is not a storage transaction: deletion after verification
can still race with a later download. Storage retention policies remain
responsible for the lifetime of published objects.

## Cache lookup precedes automatic image orientation

### Problem

The executor calculated and looked up the cache key before running automatic
image orientation. That key described the original image and extraction
provider, but did not identify the orientation provider, its models or the
effective preprocessing decision.

### Symptoms

The same image could reuse a previous extraction after an orientation model
upgrade or a configuration change. A result produced while orientation was
unavailable could also remain in use after the orientation provider recovered.
The new provider would not run because the earlier cache hit returned first.

### Solution implemented

For supported images with `auto_rotate` enabled, orientation now runs before
the execution cache lookup. The key includes the effective image fingerprint
and an explicit preprocessing context containing:

- The preprocessing identity version.
- The orientation provider ID and descriptor version.
- Provider and model versions reported by the orientation adapter.
- A fingerprint of the provider endpoint and configuration.
- The orientation request parameters.
- The orientation decision, including its stage and rotation metadata.

The extraction provider versions remain separate from this preprocessing
context. Requests without applicable orientation keep their previous cache
key material because the optional preprocessing field is omitted.

A changed provider, model, configuration, oriented payload or recovery decision
therefore cannot silently reuse the older execution result. A repeated request
with the same effective context can still reuse the extraction cache.

The tradeoff is intentional: orientation is called even when extraction is
cached. This detects changed output and recovery without relying exclusively
on version reporting. Providers should still report accurate model versions;
unreported changes that leave every observed value identical cannot be
distinguished. Variable orientation metadata can also reduce cache hit rates.

Regression tests cover provider version changes, model changes, configuration
changes, different oriented bytes and recovery after a provider failure. They
also verify stable cache reuse and that provenance fingerprints describe the
bytes actually delivered to the extractor.

## Local fallback storage is not shared across replicas

### Problem

`FailoverArtifactStore` previously wrote to a filesystem backup automatically
when S3 was unavailable. On separate hosts, that filesystem can be private to
one replica. An upload could be acknowledged by replica A even though replica
B could not retrieve the returned artifact ID.

A Docker volume with the same name on two hosts does not make their contents
shared.

### Symptoms

During an S3 outage, an upload succeeds on A but a subsequent operation on B
cannot find the artifact. Losing A can make the object unavailable even while
other replicas remain healthy. Retrying against independent disks does not
restore the missing object.

### Solution implemented

Fallback writes now require an explicit policy. The REST factory reads
`TOOLBOX_ARTIFACT_FALLBACK_MODE`; Python callers can pass `fallback_mode` to
`FailoverArtifactStore`.

| Mode | Behavior |
| --- | --- |
| `disabled` (default) | Propagate primary write failures; REST returns HTTP 503 for S3 unavailability instead of acknowledging a replica-local upload. Existing backup reads remain available when the S3 store is configured. |
| `local` | Permit filesystem fallback for a single-instance deployment. Cross-host availability is not guaranteed. |
| `shared` | Permit fallback to a filesystem that the operator has made accessible to every replica. |

Invalid modes are rejected as configuration errors. If no primary can be
configured, filesystem-only storage also requires an explicit `local` or
`shared` choice. Otherwise no artifact store is selected.

Both Compose configurations pass the new variable, and `.env.example` records
the safe default and volume source. Installation documentation explains the
migration and the shared mount requirements.

For a deployment that must continue accepting uploads across hosts during an
S3 outage, first provision the shared filesystem, then configure every replica:

```dotenv
TOOLBOX_ARTIFACT_FALLBACK_MODE=shared
ARTIFACT_FALLBACK_SOURCE=/mnt/toolbox-shared
```

The path must be an actual shared mount, writable by the container user and
supporting atomic file replacement. The application does not provision or
verify a distributed filesystem. Prevent a missing mount from silently being
replaced by an ordinary local directory.

Tests exercise two REST applications with independent directories and an
unavailable primary: the default policy refuses the upload with HTTP 503 and
does not write a local artifact. With separate store objects pointing at a
common directory in `shared` mode, B can download and verify an upload from A
after A's application is stopped. Backup reads also work after S3 recovery
when the object is absent from S3.

The shared-directory test models a shared volume locally; it does not validate
a real multi-host filesystem or live MinIO outage. Deployment acceptance must
repeat upload on A and download on B across actual hosts, including after A
stops. No automatic replication of fallback objects back to S3 was added.

## Verification

The broad regression run covered the executor, PDF/UA and DOCX exports,
artifact storage, orientation, cache identity, statelessness and REST
integration: **214 passed, 4 skipped**. The skipped integration tests depend
on unavailable external tools. Subsequent focused checks passed the added
backup-read regression and both two-replica REST scenarios.

The cached-artifact and orientation regressions failed before their corrections.
The fallback default-policy test also failed before the change because a private local
write succeeded during the simulated S3 outage.

Ruff checks passed for all changed Python files, and `git diff --check` passed.
Tests reported a dependency deprecation warning from the FastAPI/Starlette
test client. Production multi-host deployment verification remains an
infrastructure task as described above.
