# Shared artifact fallback across Toolbox hosts

The supported multi-host policy is a persistent shared filesystem: every
replica must be able to retrieve any artifact accepted by another replica,
including uploads accepted while MinIO is unavailable. Independent local
fallback disks do not satisfy that policy (see the [B08 reproduction](b08-storage-reproduction.md)).

Both Compose files provide an opt-in NFSv4 volume named `artifact-shared`.
Docker mounts an external NFS export through its local volume driver; despite
the driver's name, the data lives on the NFS server, not on each Docker host.
This does not provision an NFS server or make an existing local disk shared.

## Provision and configure

Provision a persistent NFSv4 export on infrastructure independent of MinIO's
failure domain. Configure network access, server-side permissions and storage
capacity for all Toolbox hosts. Avoid exposing the export publicly. The
container's runtime user must be able to create and replace files in it.

Set the following in **every** Toolbox deployment's `.env`, using the same
actual server and export (replace the examples with provisioned values):

```dotenv
ARTIFACT_FALLBACK_SOURCE=artifact-shared
ARTIFACT_NFS_SERVER=storage.internal
ARTIFACT_NFS_EXPORT=/exports/acessilia-artifacts
```

The normal Compose and updater commands continue to work: no extra override
file is needed. With `artifact-shared` selected, Docker must mount the NFS
export to start the container; it does not silently substitute a local
directory if the mount fails. Without this opt-in, the existing single-host
`artifact-fallback` volume remains the default.

If changing the server/export of an existing Docker volume, Docker does not
rewrite that volume's driver options automatically. Plan a maintenance window
and an explicitly provisioned replacement volume/deployment; inspect actual
driver options rather than assuming that editing `.env` changed the mount.
Never delete the old volume until its artifacts have been migrated and checked.

## Existing artifacts

Before changing a live deployment, stop new uploads/jobs in a maintenance
window. Back up and copy artifacts **and their `.meta.json` files** from every
old fallback volume into the new export, retaining the shard directory
structure. Check content hashes and verify known artifact IDs through each
replica before removing or retiring any old volume. Switching volumes alone
does not migrate existing artifacts or cached references.

Review `docker compose --env-file .env -f docker-compose.staging.yml config`
on the server, without posting its output (it contains credentials). Confirm
the selected mount is `artifact-shared`, then start the deployment with its
existing operational procedure. The automatic updater uses the same Compose
volume definitions. Keep each deployment's established Compose project name.

## Failure behavior

If MinIO fails, writes go to the shared export. Completed fallback writes are
readable through any replica that can reach that export. If filesystem writes
fail, including metadata persistence, the API returns HTTP 503 with code
`provider_unavailable` and an actionable message instead of a success response
and artifact ID. Clients can retry after recovery; identity remains based on
content. Filesystem paths are not included in that public error message.

NFS is mounted `hard` to avoid treating transient failures as completed I/O.
An already-mounted but unreachable server may block filesystem operations:
this change does **not** promise a bounded 503 response for an NFS hang.
Use client request timeouts, storage monitoring and redundant storage/network
infrastructure. The shared backend itself needs an availability design;
using one unprotected NFS server moves, rather than eliminates, a failure risk.
Artifacts written to the fallback are not automatically copied to MinIO.

## Acceptance in an isolated deployment

1. On two different Docker hosts, inspect the actual NFS mount and confirm
   the same backing server/export. Matching volume names alone are insufficient.
2. Make test MinIO unavailable. Upload through replica A; record the artifact ID.
3. Download through replica B; compare bytes/hash, metadata and ETag.
4. Replace A's container; repeat the download through B and the replacement A.
5. Restore MinIO without uploading the object there; downloads must still work
   through the shared fallback.
6. Inject a returned filesystem write error (such as exhausted capacity) in
   an isolated test setup. An upload must return 503 with no success reference.
   After recovery, retry and verify the resulting artifact.

Do not stop production MinIO or fill production storage for this test.
The local TestClient tests simulate S3 failures and a shared directory; they
do not replace this real cross-host acceptance test. Deployment and that test
are pending until the actual NFS server/export and host access are available.
