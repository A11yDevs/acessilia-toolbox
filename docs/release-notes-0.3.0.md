# 0.3.0 compatibility notes (unreleased)

Changes that a client of the Toolbox can see, collected for the 0.3.0 release.
0.2.0 is released (tag `v0.2.0`), so its notes are not edited.

## `POST /v1/artifacts` answers with `artifact/reference@1`

The upload route now returns the published reference that `artifact.store`
declares (`schemas/reference@1.json`; ADR-0015 in the Acessilia core,
[#145](https://github.com/A11yDevs/acessilia-toolbox/issues/145)).

| Field | Before | Now |
|---|---|---|
| `artifact_id` | `sha256:<hex>` | unchanged, always canonical |
| `media_type`, `size` | present | unchanged |
| `digest` | absent | **added**: equals `artifact_id` |
| `store` | absent | **added**: `storage_backend`'s value (`s3`, `filesystem`, ...), or `null` without a verified location; never `failover` |
| `storage_backend` | present | **removed** from this route: read `store` |
| `uri` | present | **removed** from this route |
| `filename` | present | **removed** from this route |

Unchanged:

- `GET /v1/artifacts/{artifact_id}/metadata` still returns the stored record,
  with `uri`, `storage_backend` and `filename`.
- The `artifacts` of an execution result keep the stored-record shape.
- The download still names the file in `Content-Disposition`.
- Records written by earlier versions (`.meta.json`, cache entries) are read
  as they are.

Migration: a client that read `uri`, `storage_backend` or `filename` from the
upload response reads them from the metadata route instead, or uses `store`.
The Acessilia SDK's `upload` reads only `artifact_id` and works with both
versions.
