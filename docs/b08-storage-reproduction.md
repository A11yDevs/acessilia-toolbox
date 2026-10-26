# B08 — Upload succeeds, but another replica cannot retrieve the file

## Brief description

When MinIO/S3 became unavailable, the Toolbox saved uploaded files on the
receiving server's local disk and reported success. Another Toolbox replica
with an independent disk could not retrieve those files.

The behavior was reproduced in an isolated test. It depends on replicas using
independent local storage; it is not a confirmed production incident.

## Symptoms

- An upload is accepted, but later processing or downloading through another
  replica fails.
- During the central-storage outage, the other replica returns HTTP 503.
- After MinIO recovers, that replica can return HTTP 404 because the file was
  never stored centrally.
- Whether a file can be retrieved depends on which replica receives the request.

## Implemented solution

**Use MinIO/S3 exclusively whenever central artifact storage is configured.**
The application no longer saves files locally as a fallback.

- During a runtime outage, uploads return HTTP 503 with a message asking the
  caller to try again later, without confirming success or returning an artifact ID.
- Invalid or incomplete central-storage configuration prevents startup instead
  of silently selecting local storage.
- After recovery, a successful retry stores the file centrally so another replica
  using the same endpoint and bucket can retrieve it.
- Explicit filesystem-only configurations remain available for local development.
- No NFS or new runtime dependency is required. Artifact IDs and public schemas
  remain unchanged.

The tradeoff is explicit: new uploads cannot succeed while central storage is
unavailable. This follows the central-storage ownership described in
[ADR-0006](https://github.com/A11yDevs/acessilia/blob/release/0.2.0/docs/adr/0006-availability-and-failover.md).

**Validation:** four regression cases failed before the selection fix. After
correction, **1,032 unit tests** and **88 targeted tests** passed with no skips.
The targeted suite overlaps the unit suite; the counts must not be summed.
Lint, formatting and type checks on the changed application/storage files passed.
The integration tests use two REST apps and a local HTTP server simulating S3;
real MinIO and production deployment tests have not been run.

**Deployment:** prepared for review, not deployed. Existing local artifacts and
metadata must be backed up and migrated before rollout. Old volumes are retained.
See the [migration procedure](artifacts.md#migrating-existing-fallback-artifacts).

Reference: Toolbox `release/0.2.0`, base `8f33ea6f165657594433684da6f650065bc46a57`;
audit reference `1a1a866b7a43995f1b1d46f599dd69a0715b69b6`. Date: 2026-10-26.
