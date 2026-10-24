# Automatic Image Update (Auto-Update)

The staging server runs from a **clone of this repository**: the clone holds the scripts, `docker-compose.staging.yml`, `.env` and `var/`, and the containers run the images CI publishes on GHCR. A **systemd user timer** updates the toolbox container and its sidecars together within 5 minutes of the images of a new commit on the tracked branch (`develop`, or `release/x.y.z` during BHS) being published.

Everything goes through `scripts/ops.sh`:

| Command | What it does |
|---------|--------------|
| `./scripts/ops.sh auto-update setup` | Starts the stack and installs the timer |
| `./scripts/ops.sh auto-update status` | Clone branch, tracked branch, timer, last check, health, recent runs |
| `./scripts/ops.sh auto-update check` | Verifies the timer (installed for this clone, enabled, old timer gone), its recent runs (last one succeeded, last check recent, latest commit deployed) and the deployment (branch, container, image, health); exit status 1 on a problem |
| `./scripts/ops.sh auto-update start` | Updates now (what the timer runs) |
| `./scripts/ops.sh track [<branch>]` | Follows a branch: `git pull` of the clone and `TRACK_BRANCH` in `.env`, then updates; without a branch, pulls the tracked one |
| `./scripts/ops.sh auto-update migrate <dir>` | Moves an install made by the former `setup-homologacao.sh` into the clone |

## How it works

```
[Git push] → [GitHub Actions: CI + Delivery]  → [GHCR: toolbox image develop, sha-<7>]
           → [GitHub Actions: Sidecars]       → [GHCR: acessilia-toolbox-<sidecar> develop, sha-<7>]
                                                      ↑
[systemd timer (5 min)] → [ops.sh auto-update start → staging-update.sh]
                              │
                              ├── 1. Queries the GitHub API (SHA of the latest commit)
                              ├── 2. Confirms the toolbox image sha-<7> is published
                              ├── 3. Confirms the sha-<7> image of every sidecar it runs is published
                              └── 4. docker compose pull + up -d (toolbox and sidecars)
```

The sidecars it manages are those with a container on the host whose image is
the CI-published one. A sidecar whose `*_IMAGE` variable points at a local
build is left alone (see [installation](installation.md#sidecar-images)).

### Moving a server from hand-built sidecars

Sidecar images used to be built on each server. To switch a deployment to the
CI-published images, once per server:

```bash
# 1. Drop the local image overrides (MINERU_SERVE_IMAGE=mineru:latest, ...)
sed -i -E '/^(MINERU_SERVE|NOUGAT_SERVE|AUDIVERIS_SERVE|RAPID_LATEX_OCR|PP_FORMULANET|RAPIDOCR_ORIENT|PDF_UA_RENDER|VERAPDF_VALIDATE)_IMAGE=/d' .env

# 2. Pull the clone (new compose file) and start the sidecars from GHCR
./scripts/ops.sh track
docker compose -f docker-compose.staging.yml pull
docker compose -f docker-compose.staging.yml up -d
```

## Installation

### First install

```bash
git clone https://github.com/A11yDevs/acessilia-toolbox.git
cd acessilia-toolbox
cp .env.example .env              # fill in MINIO_ACCESS_KEY, MINIO_SECRET_KEY, TOOLBOX_API_KEY...
echo <token> | docker login ghcr.io -u <your-user> --password-stdin   # read:packages
./scripts/ops.sh auto-update setup
```

`setup` sets `COMPOSE_PROJECT_NAME=acessilia-toolbox-staging` in `.env`, so the containers and named volumes belong to the same compose project whatever the clone's directory is called. It then starts the stack and enables `acessilia-toolbox-staging-update.timer`.

The user that runs the timer needs, granted once by an administrator, membership in the `docker` group (`usermod -aG docker <user>`, then log in again; `setup` and `migrate` refuse to run without access to the Docker daemon) and linger (`loginctl enable-linger <user>`, so the timer keeps running after logout; `setup` warns when it is off). `ops.sh` never calls `sudo`; if something must run as root, run that command (or `ops.sh` itself) as root. A session opened with `su` has no `XDG_RUNTIME_DIR`; `ops.sh` then uses `/run/user/<uid>` by itself, and when that does not exist either (no linger, no login), `setup` and `migrate` stop and say so.

### Migrating from `/opt/a9a-toolbox/staging`

Installs made by the former `setup-homologacao.sh` copied the scripts next to the deployment and kept `.env` and `var/` in `/opt/a9a-toolbox/staging` (or `/opt/acessilia-toolbox/staging`). To move one into a clone:

```bash
git clone -b release/0.2.0 https://github.com/A11yDevs/acessilia-toolbox.git ~/acessilia-toolbox
cd ~/acessilia-toolbox
./scripts/ops.sh auto-update migrate /opt/a9a-toolbox/staging
```

`migrate` first checks everything it needs: that it can read the old `.env` and all of `var/` (it lists what it cannot read), that the clone has no different `.env`, that the branch in `TRACK_BRANCH` can be checked out, and that Docker and the user's systemd instance are reachable. Any failure stops it before anything changes, and a run interrupted later can simply be repeated. It then disables the old `staging-update` timer (stopping if it cannot), copies `.env` and `var/`, keeps the compose project name of the running toolbox container so compose takes over its containers and **named volumes** (MinIO data, model caches) instead of creating new ones, checks out the branch in `TRACK_BRANCH` and runs `setup`. The old installs kept the Toolbox and Acessilia in one project named `staging`: `migrate` warns when the project holds another stack's containers, which a `--remove-orphans` here would delete. Acessilia's own `migrate` moves it to a project of its own. The old directory is left untouched; remove it once staging works.

## Management

```bash
./scripts/ops.sh auto-update status          # overview
./scripts/ops.sh auto-update check           # pass/fail verdict on the timer and its runs
./scripts/ops.sh auto-update start           # update now
./scripts/ops.sh track                       # git pull of the tracked branch, then update
journalctl --user -u acessilia-toolbox-staging-update.service -n 50 --no-pager
systemctl --user stop acessilia-toolbox-staging-update.timer    # pause updates
systemctl --user start acessilia-toolbox-staging-update.timer   # resume them
```

`track` refuses to run when tracked files in the clone have local changes, so the server never drifts from the repository. To test a locally built image, stop the timer first: otherwise the next update pulls the GHCR image over it.

### Check which images are running

```bash
docker compose -f docker-compose.staging.yml images
docker inspect pdf-ua-render-staging | jq -r '.[0].Config.Labels["org.opencontainers.image.revision"]'
```

## Branch tracking (BHS)

During the Bug Hunting/Squashing (BHS) cycle, staging follows the release branch:

```bash
./scripts/ops.sh track release/0.2.0     # before Acessilia's staging
```

It checks out and pulls the branch, sets `TRACK_BRANCH` and `TRACK_TAG` in `.env` (so a plain `docker compose` in the clone uses the same images) and updates. When the cycle ends, once the release's sync PR is merged into `develop`:

```bash
./scripts/ops.sh track develop
```

`track` refuses a branch without `scripts/ops.sh`, since the timer runs it from the clone.

## Status files

The script maintains status files in `var/data/`:

- `var/data/.last_sha` — tracked branch and SHA of the last commit that triggered an update
- `var/data/staging-status.json` — JSON status with current SHA, last check and last update

```bash
cat var/data/staging-status.json | jq .
```

Example output:

```json
{
  "latest_sha": "abc123def456...",
  "running_sha": "abc123def456...",
  "last_check": "2025-09-07T14:30:00Z",
  "last_update": "2025-09-07T14:25:00Z"
}
```

## Troubleshooting

### Container not updating

1. Check if the GHCR token has `read:packages` permission
2. Check the overview and logs: `./scripts/ops.sh auto-update status`
3. Run manually: `./scripts/ops.sh auto-update start`
4. Check if the image was published: `docker manifest inspect ghcr.io/a11ydevs/acessilia-toolbox:sha-<sha7>`

### `The container name "/<id>_<name>" is already in use`

`docker compose up` recreates a container by first creating the replacement as
`<old-container-id[:12]>_<container_name>`, and only then stopping the old one
and renaming the new one. If the update is interrupted in between (the MinerU
image alone can take minutes to pull), the temporary container stays behind and
the next `up` fails with that name conflict.

`staging-update.sh` removes these leftovers for the toolbox and the sidecars it
manages before every `up`, so a later run heals itself. To clear them by hand:

```bash
docker ps -a --format '{{.ID}} {{.Names}}' | grep -E '^[0-9a-f]{12}_'
# inspect the list, then remove the containers that start with the id prefix:
docker rm -f <container-id> [...]
./scripts/ops.sh auto-update start
```

Two updates never run at the same time: the script takes a lock, so a manual
`auto-update start` during the timer's run exits with
`Another update is already running. Skipping.`

### docker compose not found

The script uses `docker compose` (plugin). If your environment uses `docker-compose` (standalone), set:

```bash
export COMPOSE_FILE="docker-compose.staging.yml"
docker-compose up -d
```

### `acessilia-toolbox-staging belongs to compose project '…'`

`track`, `auto-update start` and `setup` refuse to run when the toolbox container belongs to another install, which would make compose fail with a name conflict. The message names that install's directory; move it into the clone with `./scripts/ops.sh auto-update migrate <dir>`.

### Linger not enabled

The user timer won't survive logout if linger is not active. `ops.sh auto-update setup` warns when it is off; an administrator enables it with `loginctl enable-linger <user>`. To check:

```bash
loginctl show-user $USER | grep Linger
```