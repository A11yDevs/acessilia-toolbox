# 0.2.0 storage and cache compatibility notes

## S3 fallback writes require an explicit policy

`TOOLBOX_ARTIFACT_FALLBACK_MODE` defaults to `disabled`: an unsuccessful S3
upload remains an error instead of silently creating a replica-local object.
Single-instance deployments relying on the previous automatic filesystem
fallback must select `local`. Multi-host deployments needing outage writes
must provision and verify a shared mount before selecting `shared`.

This policy applies to `FailoverArtifactStore`. Filesystem-only installations
keep using filesystem as their primary store without an extra opt-in; startup
logs a warning, since replicas on different hosts then need a shared mount.
An invalid mode remains a startup configuration error.

## Inline results remain usable during storage outages

Inline-only JSON operations may return their document without a persisted
download location during temporary storage failures. Warm inline-only cache
entries remain usable without repeating extraction when storage cannot be
verified. Newly extracted unpersisted results are cached too, so a prolonged
outage does not cost one extraction per request; once storage recovers, the
missing object invalidates the entry and the next request republishes it.
Missing objects that can be verified invalidate the cache; required
binary/by-reference artifacts still require storage availability.

## Orientation identity

Orientation confidence no longer changes extraction cache identity; it remains
available in provenance. RapidOCR version probes are memoized for 30 seconds
per process, with last-good identity retained during failed refreshes and a
5-second retry delay. Other requests are served the last-good identity while a
refresh is in flight, so a slow version endpoint does not delay them. Version changes may take up to the TTL to be observed.
The effective image and orientation decision are still evaluated per request.

See [installation and operation](installation.md) for environment variables,
shared-mount requirements and rollout checks.
