# B08 — Central-only artifact storage

Date: 2026-10-26. Target: Toolbox `release/0.2.0`.
Base: `8f33ea6f165657594433684da6f650065bc46a57`.
Audit reference: `1a1a866b7a43995f1b1d46f599dd69a0715b69b6`.

## Problem and initial evidence

The old `FailoverArtifactStore` accepted uploads to a host-local filesystem
when MinIO failed. Another replica with an independent filesystem could not
retrieve them: the initial two-app HTTP experiment returned 503 during the
outage, then 404 after the central store recovered without the object.
A shared-directory control succeeded. This reproduced a conditional topology
risk, not a confirmed production incident. Historical deployment evidence is
available in [PR #42](https://github.com/A11yDevs/acessilia-toolbox/pull/42) and
[PR #79](https://github.com/A11yDevs/acessilia-toolbox/pull/79); neither proves a
current multi-host deployment.

## Selected correction

After evaluating shared NFS, the user chose the simpler central-only policy:

- If any S3 artifact store is configured, select central storage only. Do not
  initialize or use a local backup, even when central initialization fails.
- Fail startup on unusable central configuration. For runtime outages, return
  503 without acknowledging an upload or returning an artifact success reference.
- Remove the runtime failover class; retain the filesystem descriptor as an
  explicit local-development alternative without changing the public catalogue. Remove the NFS configuration proposed earlier in this PR.
- Permit explicit filesystem-only provider configurations for local development.
- Keep existing local volume declarations to avoid deleting historical data;
  migrate historical local artifacts before rollout, as described in
  [artifact storage](artifacts.md#migrating-existing-fallback-artifacts).

This implements shared central artifact ownership and explicit unavailability
from [ADR-0006](https://github.com/A11yDevs/acessilia/blob/release/0.2.0/docs/adr/0006-availability-and-failover.md).
It changes availability policy: central storage must work for uploads to succeed.
It does not add cross-site replication, repair independent S3 deployments, or
make MinIO itself highly available. Artifact identity and wire schemas remain
unchanged. A configured central store accepting an object is the application's
success condition; actual durability still depends on that store's deployment.

## Before/after evidence

Before changing selection, four new regression cases failed: central selection
still initialized a local backup, broken S3 construction did not fail closed
(two exception types), and unresolved S3 configuration selected local storage.
The filesystem-only development control passed. These logs are retained locally
in `/tmp/b08-central-before.log`.

`tests/integration/test_artifact_central_storage.py` exercises two independently
constructed REST apps using real S3 adapters against a local fault-injection
HTTP server. It verifies rejected uploads on both replicas during an outage,
no local directories created, no objects accepted, recovery and retry, and
retrieval of original bytes/metadata through another replica and a replacement
app. Further outages return 503 without falling back to disk.

This is not a real MinIO deployment or a two-host durability test. It uses no
NFS and requires no production outage. Runtime network/timeout tests and the
existing storage/API/replica-replacement regressions are also retained.

## Rollout status

Code and tests are prepared for review; production has not been changed.
Operators must migrate existing fallback artifacts before upgrading, configure
all replicas against the same MinIO bucket, and validate the policy in an
isolated deployment. No new NFS infrastructure is needed.

## Final validation

- Before the selection change: 4 regression cases failed, 1 local-development
  control passed.
- Full unit suite: **1,032 passed**, no skips.
- Targeted storage, registry, REST (including OpenAPI/auth), network timeout,
  artifact API and replica-replacement suite: **88 passed**, no skips. This
  overlaps the unit suite and should not be added to it as a unique-test count.
- Ruff check across `src/`, `tests/`, `scripts/`: passed.
- Mypy for the modified application factory and storage adapter: passed.
- Production and live MinIO deployment tests have not been run.

The factory now honors explicitly supplied empty registries instead of loading
production defaults, so schema/auth tests and deliberately storage-free apps
remain independent of central storage configuration. No capability schema or
published contract snapshot changed.
