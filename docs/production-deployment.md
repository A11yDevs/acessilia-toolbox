# Production Toolbox deployment

Production uses `docker-compose.production.yml`, never the staging Compose or
staging updater. The default deployment directory is `/opt/acessilia/toolbox`;
the core has its own configuration and data under `/opt/acessilia/core`.
The Compose project remains `acessilia-toolbox-production`, preserving existing
named volumes when adopting this configuration on VM1.

## Configuration and isolation

- `.env` holds runtime secrets. `TOOLBOX_API_KEY` must match the core's key.
  `VLM_API_KEY` must match the model-host gateway's **TOOLBOX_API_KEY**, not the
  gateway's upstream model key. Generate independent production credentials;
  do not copy the staging Toolbox key or MinIO credentials.
- `.deploy.env` holds the separately approved digest of every image. The initial
  example records the complete image set validated during the VM1 0.2.0 migration
  (Toolbox source `34802e44109f0324d16428d780dbedee0f7984ab`). These are an explicit
  baseline, not an instruction to follow a release tag or rebuild all images.
- The production registry omits unused Nougat and Audiveris providers and their
  services. All retained services have `*-production` names. No host ports are
  published, including the Toolbox API, sidecars, MinIO and Valkey.
- Only the Toolbox joins the external `acessilia-production-backplane` network
  (override `PRODUCTION_TOOLBOX_NETWORK` in `.deploy.env` to match the core), with
  alias `toolbox-production`. The core uses `http://toolbox-production:8002`.
  Providers, cache and object storage stay on the private project network.
- The VLM endpoint must be the authenticated gateway's `/v1/vlm` route. On the
  model host, bind the model to loopback and run exactly one gateway process with
  `VLM_MAX_CONCURRENCY=2`, matching the model's two parallel slots. The local
  Toolbox limit of four does not replace that shared global limit.

## Prepare without starting services

Run as the deployment user with Docker access, from a reviewed checkout:

```bash
umask 077
mkdir -p /opt/acessilia/toolbox
# New installation only: do not overwrite any existing file or credential.
cp -n docker-compose.production.yml /opt/acessilia/toolbox/
cp -n deploy/production/providers-config.yaml /opt/acessilia/toolbox/
cp -n deploy/production/runtime.env.example /opt/acessilia/toolbox/.env
cp -n deploy/production/deploy.env.example /opt/acessilia/toolbox/.deploy.env
chmod 600 /opt/acessilia/toolbox/.env /opt/acessilia/toolbox/.deploy.env
```

Fill secrets and `VLM_SERVE_URL` privately. Check all image digests against the
approved inventory. On an existing installation, back up the current files and
compare them with these templates; preserve keys, provider settings, volume
names, image digests and the Compose project. The migration's out-of-repository
VM1 package is the baseline for these versioned files, not another production
installation to run beside it. Adopting the templates is a separate manual
change; this PR does not change running containers or timers.

```bash
./scripts/production-update.sh --check
```

This resolves the real Compose configuration and rejects tags, wrong container
identities, missing secrets, raw-model URLs, published ports, missing aliases,
and deployment credentials injected into the Toolbox. It does not prove that a
secret is valid or that an upstream service is reachable. It never starts a
service. Do not print resolved Compose JSON because it contains credentials.

Create the external network once if it does not already exist:

```bash
docker network inspect acessilia-production-backplane >/dev/null 2>&1 ||
  docker network create --driver bridge acessilia-production-backplane
```

## Explicit promotion and acceptance

The old production wrapper tracked `main` through the staging updater and
looked for a container that its Compose never created. It is replaced with
manual digest promotion: **no arguments now means check only**. Disable any
legacy production timer before adopting it; no timer is installed or enabled by
this workflow. `PRODUCTION_DIR` can select another deployment directory. The
gateway is deployed separately and is never updated by these commands.

1. Suspend new submissions in every environment using this Toolbox and drain
   queued **and running** jobs. A health queue size of zero alone is insufficient.
2. Back up configuration, the previous complete digest inventory and persistent
   volumes consistently before changing the stack. Retain the old images. Size
   disk and RAM using actual documents; first-use models may download to volumes.
3. Review the complete `.deploy.env` image set and runtime/registry changes.
4. Run `./scripts/production-update.sh --apply`. It validates, pulls the full
   pinned image set, runs Compose with `--wait`, checks every running image ID
   and service health, and probes Docling, MinerU and the authenticated VLM chain.
5. Test a representative document in dual fusion, required exports, interfaces,
   e-mail, retained download links and expired-link rejection before reopening
   traffic. Health is not a substitute for this acceptance.

A failed pull leaves the running containers unchanged. A failed activation or
post-start probe can leave **new containers running**: keep traffic suspended,
inspect actual state and follow manual rollback. There is no automatic data
restore, success cache, image pruning or volume deletion. To roll back, restore
the previously approved configuration/image set and compatible persistent data
from the offline backup when necessary; verify dependencies before reopening
traffic. Never reopen direct model access as a rollback shortcut.

## Staging and BHS closure

Staging keeps its authenticated Toolbox API reachable by the core; auxiliary
ports now bind to `127.0.0.1` for local diagnostics only. Containers still use
Compose DNS to reach sidecars, MinIO and Valkey. For remote diagnostics, use an
SSH tunnel rather than publishing unauthenticated services on every interface.

Merge the Toolbox release into `main` with a merge commit and synchronize its
fixes into `develop` before closing the core release. Switch staging to
`develop` **before** `bhs finish --cleanup` deletes the release/sync branches.
The resulting tag and image publications do not promote production: retain its
approved digests until a separate acceptance and manual promotion. Resolve
sync conflicts on `chore/sync-release-0.2.0`, never by merging `develop` into the
release. Production deployment fixes added during closure must also reach the
sync branch through the reviewed release-fix PRs.
