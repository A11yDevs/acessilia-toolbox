# B08 — Local fallback across replicas: reproduction and triage

Date: 2026-10-26. Branch: `fix/b08-shared-artifact-storage`, based on Toolbox
`release/0.2.0` at `8f33ea6f165657594433684da6f650065bc46a57`.
The audit cited Toolbox `1a1a866b7a43995f1b1d46f599dd69a0715b69b6`;
the relevant failover behavior is also present at the tested revision.

## Status

**Reproduced, conditional on independent fallback filesystems. Remediation
prepared locally; not deployed or verified across real hosts.**
The initial investigation added characterization tests before changing the
implementation. The follow-up adds an opt-in shared NFS volume to both Compose
files and maps filesystem write failures to an actionable 503 response without
a success reference. See [deployment and migration](shared-artifact-storage.md).
Public artifact identity is unchanged.

`FailoverArtifactStore.put()` accepts a write on the filesystem when S3 is
unavailable. Another replica can only retrieve it if that replica can access
the same bytes through its own configured stores. Artifact identity does not
replicate content or locate the producing replica.

The existing policy already requires shared persistent storage for replicas
on different hosts: see [artifacts](artifacts.md#minio) and
`ARTIFACT_FALLBACK_SOURCE` in [installation](installation.md).
The default Compose named volume does not provide cross-host sharing.
Filesystem artifacts are explicitly not copied back to S3 automatically.

## Experiment

`tests/integration/test_artifact_replica_fallback.py` creates two independent
REST applications, each with its own S3 adapter and filesystem adapter.
A local HTTP server returns S3-shaped `503 ServiceUnavailable` responses.
After simulated recovery it returns `404 NoSuchKey`: it is reachable again,
but the locally stored object was never uploaded there.

Both configurations upload through A and read through A and B:

| Observation | Independent directories | Common directory |
| --- | --- | --- |
| Upload through A during S3 outage | 200; filesystem reference | 200; filesystem reference |
| Read through A during outage | Original bytes | Original bytes |
| Read through B during outage | 503 `provider_unavailable` | 200; original bytes and matching ETag |
| Read through B after S3 recovery without migration | 404 `artifact_not_found` | 200; original bytes |
| Recreate A with its original filesystem, S3 offline | 200; original bytes | 200; original bytes |

The two parametrized tests assert these observed outcomes and pass. They
**do not** assert that isolated replicas are safe. To demonstrate the missing
guarantee explicitly, a temporary copy of the isolated-directory test required
HTTP 200 from B during the outage. It failed with `assert 503 == 200`, before
any production changes. The temporary test and execution logs are outside the
repository under `/tmp`.

Reproduce the characterization from the repository root:

```sh
.venv/bin/python -m pytest tests/integration/test_artifact_replica_fallback.py -v
```

The fixture injects S3 failures over real local HTTP and exercises the real
S3 adapter and REST handlers. Requests to the two Toolbox apps use TestClient.
This is not a two-host deployment, a real MinIO outage or a test of NFS/Ceph
mount behavior, distributed durability, authentication or network partitions.
Docker is not installed in the current development environment.

Validation on the unmodified production implementation:

- New characterization: 2 passed.
- Storage unit tests, artifact API integration tests, S3 timeout/failover tests,
  replica-replacement tests and new characterization: 35 passed.
- Live S3 contract tests: 9 skipped because no MinIO service is running.
- Temporary cross-replica availability assertion: 1 failed as expected
  (`503 == 200`); this is reproduction evidence, not an unexpected regression.
- Ruff lint and format checks on the new test and `git diff --check`: passed.

These are targeted checks, not a full Toolbox test-suite validation.

Follow-up implementation validation:

- Before changing `FilesystemArtifactStore`, both payload-write and
  metadata-write error tests failed with an uncaught `OSError`.
- After the change, the API returns 503 without an artifact success reference;
  retries after recovery succeed with the content-derived ID. Both tests pass.
- Storage unit tests, shared-volume configuration tests, artifact API and
  failover integration tests, and replica-replacement tests: 39 passed.
- Ruff check across `src/`, `tests/`, `scripts/`: passed.
- Docker Compose v5.6.1 parsed both deployment files with the shared option
  selected; the normalized models use `artifact-shared` and the same NFSv4
  server/export. A documentation-only test address was used. No mount or Docker
  daemon was involved, so this is configuration validation, not deployment.

## Decision required for remediation

The documented shared-filesystem policy is the smallest existing route to
cross-replica visibility. To apply it to a real deployment, identify the hosts,
volume driver and actual mounts, provision shared persistent storage, and point
every replica at that same backend. Identical path strings alone are not proof
of shared storage. Validate cross-host write/read and replica replacement with
S3 unavailable before declaring B08 resolved.

If independent local disks must remain supported, a different explicit design
is needed: routing to the producer, replication/migration, or rejecting writes
whose cross-replica availability cannot be guaranteed. Routing alone cannot
survive loss of the producing host; later migration alone does not make the
object available on B during the outage. Any change to artifact references or
SDK routing must be coordinated with B05/B07 and the other audit track.

The selected remediation follows the existing shared-filesystem policy;
production infrastructure has not been provisioned or deployed here. A
successful common-directory control is evidence of the required visibility
semantics, not evidence that production has that topology.

## Deployment inspection

Additional GitHub investigation found historical evidence of an actual staging
host, but not confirmation of a current multi-host replica deployment:

- [Toolbox PR #42](https://github.com/A11yDevs/acessilia-toolbox/pull/42)
  reports validation on the staging server under `/opt/a9a-toolbox/staging`,
  with the updater running as `a9a-toolbox`.
- [Toolbox PR #79](https://github.com/A11yDevs/acessilia-toolbox/pull/79)
  describes the real staging host running the Acessilia and Toolbox Compose
  stacks and preserving the Toolbox project's named volumes during migration.
  This establishes a reported deployment, not the current number of hosts.
- [ADR-0006](https://github.com/A11yDevs/acessilia/blob/release/0.2.0/docs/adr/0006-availability-and-failover.md)
  requires replicas behind a balancer with shared storage/cache, but describes
  the replicated deployment as infrastructure work still needed and its
  two-replica E2E check as planned. An accepted design is not deployment proof.
- The GitHub deployments and environments collections for the Toolbox were
  empty when queried on 2026-10-26. This does not prove that there is no live
  deployment; the updater runs on the host rather than recording it there.

Working triage: the reported staging deployment uses Compose and persistent
volumes; the GitHub evidence inspected does not establish multiple Toolbox
hosts with independent disks. B08 remains a reproduced conditional risk, not
a confirmed incident in that deployment. Prefer verifying/enforcing the
existing shared-storage policy before introducing SDK routing or replication.

The versioned `docker-compose.yml` and `docker-compose.staging.yml` each
declare one `toolbox` service with a fixed container name and the default
host-local `artifact-fallback` named volume. They do not provision a shared
filesystem or a multi-host cluster. The operational scripts update a local
Compose deployment; they do not establish a cross-host artifact mount.

This describes the checked-in templates, not the live deployment: environment
overrides, volume drivers and host mounts have not been inspected. This
notebook has no Docker executable, remote Docker context, or identified
deployment host available for this investigation.

On each actual Toolbox host, collect only the container identity and mounts
(do not send `.env` files or a full `docker inspect`, which may expose secrets):

```sh
docker ps --format '{{.Names}}\t{{.Image}}'
# Substitute the actual Toolbox container name from the previous command.
docker inspect --format '{{json .Mounts}}' CONTAINER_NAME
```

For the mount whose destination is `/tmp/acessilia-toolbox-artifacts`, record
its type, source, read/write setting and volume name. For named volumes, inspect
only the driver and mountpoint (driver options can contain credentials):

```sh
docker volume inspect --format '{{.Driver}} {{.Mountpoint}}' VOLUME_NAME
# Substitute the host-side source/mountpoint, not the container destination.
findmnt -T HOST_SOURCE --output TARGET,SOURCE,FSTYPE
```

Compare across hosts. The same volume name or path is insufficient evidence.
Before selecting a remedy, determine whether there are actually multiple
Toolbox hosts and whether they already share the same backing filesystem.
The final acceptance test must also show cross-replica write/read while S3 is
offline and persistence after replacing the producing container. Simulate
outages in an isolated test environment, not by stopping production MinIO.
