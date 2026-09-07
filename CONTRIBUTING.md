# Contributing to acessilia-toolbox

Thank you for considering contributing! This document defines the project guidelines based on a **simplified Git Flow**, designed to maintain the agility typical of open source projects.

## Branch model

```
main  ──────────────●──────────────────●──  (versões estáveis)
   \                /  \                /
    develop ─────●─── release/x.y.z ─●──── (integração / BHS)
         \      /  \       |       /
          feat/*──   fix/*─┴──────
```

### Permanent branches

| Branch | Purpose |
|--------|------------|
| `main` | **Production.** Stable, reviewed code. Only merges from `develop` or `hotfix/*`. |
| `develop` | **Integration.** Where features under development converge. Default collaboration branch. |

### Temporary branches

| Prefix | Purpose | Originates from | Merges into |
|---------|------------|----------|------------|
| `feat/*` | New feature | `develop` | `develop` |
| `fix/*` | Bug fix | `develop` | `develop` (or `release/*` during BHS) |
| `docs/*` | Documentation | `develop` | `develop` |
| `refactor/*` | Refactoring | `develop` | `develop` |
| `chore/*` | Maintenance (deps, CI, config) | `develop` | `develop` |
| `release/*` | Release stabilization (BHS cycle) | `develop` | `main` and `develop` |
| `hotfix/*` | Critical production fix | `main` | `main` and `develop` |

> **Important:** Temporary branches must be deleted after merge.

### Roles and permissions

| Role | Who | Permissions |
|-------|------|------------|
| **Maintainers** | [@marceloakira](https://github.com/marceloakira), [@jhonata192](https://github.com/jhonata192) and [@fragaeduardo](https://github.com/fragaeduardo) | Only ones authorized to merge `develop → main` and create releases. |
| **Contributors** | Everyone else | Can open PRs to `develop` and review. |

## Environment setup

```bash
# Clone and install dependencies
git clone git@github.com:A11yDevs/acessilia-toolbox.git
cd acessilia-toolbox

# Virtual environment
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Configure environment variables
cp .env.example .env
# Edit .env with the required credentials

# Run tests to verify everything is ok
pytest tests/unit/ -v
```

## Running locally with Docker

For detailed instructions on running the Toolbox with Docker — both with local build and with pre-published GHCR images — see the dedicated guide:

📄 [`docs/dev-workflow.md`](docs/dev-workflow.md)

## Daily workflow

### 1. Start a task

```bash
# Sync with develop
git checkout develop
git pull

# Create branch for the task
git checkout -b feat/my-feature
```

### 2. Develop

Make atomic commits following the [commit convention](#commit-convention).

```bash
git add .
git commit -m "feat(api): add export endpoint"
```

### 3. Stay synchronized

Always rebase with `develop` to avoid large conflicts:

```bash
git fetch origin
git rebase origin/develop
```

### 4. Submit for review

Before opening a Pull Request, make sure tests are passing:

```bash
pytest tests/ -v
```

The CI pipeline will run tests automatically on GitHub. The PR can only be reviewed if **all tests are green**.

```bash
# Option A — via GitHub (recommended)
git push origin feat/my-feature
# Open a Pull Request from feat/my-feature → develop

# Option B — local merge (for simple changes)
git checkout develop
git merge feat/my-feature
git push origin develop
git branch -d feat/my-feature
```

### 5. Staging (QA)

After a PR is merged into `develop`, the **CD pipeline** (`.github/workflows/delivery.yml`) automatically builds a Docker image and publishes it to GHCR with the `develop` and `sha-<commit>` tags.

The staging environment uses a **systemd timer** (`staging-update.timer`) that checks every 60s for a new image and restarts the container automatically. See [`docs/auto-update.md`](docs/auto-update.md) for detailed installation and management instructions.

The complete staging environment setup is at:

- `docker-compose.staging.yml` — defines the container
- `scripts/staging-update.sh` — update script
- `scripts/setup-homologacao.sh` — initial setup script

```bash
# Check which image is running (via health API)
curl http://localhost:8002/v1/health | jq .

# Or pull a specific image manually for testing
docker pull ghcr.io/a11ydevs/acessilia-toolbox:sha-abc1234

# Check which tag is running in the container
docker inspect acessilia-toolbox-staging | jq '.[0].Config.Image'
```

### 5.1 Bug Hunting/Squashing (BHS)

Before each release, there is a **Bug Hunting/Squashing (BHS)** cycle: a period where staging tests exactly the release candidate, without mixing features still in development. To do this, we create an ephemeral `release/x.y.z` branch from `develop`.

1. **Cut** — at the start of BHS, cut the release branch from `develop`:

   ```bash
   git checkout develop && git pull
   git checkout -b release/0.1.0 origin/develop
   git push origin release/0.1.0
   ```

2. **During BHS**:
   - PRs `fix/*` that fix bugs found in staging go to `release/0.1.0` (instead of `develop`).
   - PRs `feat/*` continue targeting `develop` normally — `develop` is never blocked, since the release scope was already locked at cut time.
   - The **CI pipeline** (`ci.yml`) runs the same tests for PRs and pushes to `release/**`.
   - The **CD pipeline** (`delivery.yml`) publishes the `release/0.1.0` image to GHCR with the `release-0.1.0` and `sha-<commit>` tags.

3. **Staging points to the release** — set `TRACK_BRANCH=release/0.1.0` in the staging server's `.env` so that `scripts/staging-update.sh` tracks the release branch (image tag `release-0.1.0`) instead of `develop`:

   ```bash
   echo "TRACK_BRANCH=release/0.1.0" >> .env
   ```

4. **BHS closing** — when the release is stable:

   ```bash
   # Merge to main (creates the official release, see section 6)
   # Open a PR from release/0.1.0 → main and merge after approval

   # Propagate fixes to develop
   # Open a PR from release/0.1.0 → develop and merge after approval

   git push origin --delete release/0.1.0
   ```

   Then remove (or revert) `TRACK_BRANCH` from the staging `.env` so staging goes back to tracking `develop`.

### 6. Release (develop → main)

Only maintainers ([@marceloakira](https://github.com/marceloakira), [@jhonata192](https://github.com/jhonata192) and [@fragaeduardo](https://github.com/fragaeduardo)) can merge `develop → main`.

1. **QA approved?** → proceed.
2. Open a Pull Request from `develop` to `main` on GitHub.
3. Request review from another maintainer.
4. After approval, merge (preferably "Create a merge commit").
5. The **CD pipeline** on `main` publishes the `main`, `latest` and `sha-<commit>` tags.

To create an **official Release** with semantic versioning:

```bash
# 1. Update the version in pyproject.toml
#    (e.g. bump from "0.1.0" to "0.2.0")
git checkout main && git pull
# edit pyproject.toml
git add pyproject.toml
git commit -m "chore(release): bump to 0.2.0"

# 2. Create the semantic tag
git tag v0.2.0
git push origin main --tags

# 3. The Release workflow (release.yml) builds, publishes v0.2.0 to GHCR
#    and creates the GitHub Release with automatic changelog
```

After the release, merge `main` back to `develop`:

```bash
git checkout develop
git merge main
git push origin develop
```

### 7. Hotfix (critical fix)

Hotfixes follow the same PR flow, not direct push. CI creates the tag and release automatically.

```bash
git checkout main
git checkout -b hotfix/crash-upload
# apply the fix
git commit -m "fix: fix crash when uploading corrupted PDF"
git push origin hotfix/crash-upload
# Open a Pull Request from hotfix/crash-upload → main on GitHub
# After approval and merge, CI creates the main + sha-xxx image

# If critical enough to warrant immediate release:
git tag v0.1.1 && git push origin v0.1.1

# Propagate to develop
git checkout develop
git merge main
git push origin develop
git branch -d hotfix/crash-upload
```

## Commit convention

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<optional scope>): <description>

[optional body]
```

### Types

| Type | Usage |
|------|-----|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation |
| `refactor` | Refactoring without behavior change |
| `test` | Tests |
| `chore` | Maintenance (deps, CI, config) |
| `style` | Formatting, lint |
| `perf` | Performance improvement |

### Examples

```
feat(api): add export endpoint
fix(provider): fix timeout on large files (>10MB)
docs(readme): update API usage examples
refactor(core): extract cache logic to separate service
test(contract): add test for Docling provider
chore(deps): update fastapi to 0.115
```

## Versioning

We follow [Semantic Versioning](https://semver.org/):

```
vMAJOR.MINOR.PATCH
```

- **MAJOR**: incompatible API change
- **MINOR**: backward-compatible new feature
- **PATCH**: backward-compatible bug fix

## Team rules

1. **Never commit directly to `main`** — always use branches + PR.
2. **Never commit directly to `develop`** — except merges of temporary branches.
3. **Always rebase** before merging to keep a linear history.
4. **Branches are temporary** — last only as long as needed for the task.
5. **Small, focused PRs** — easier to review and with fewer conflicts.
6. **Atomic commits** — one commit = one complete logical change.
7. **Tests required** — every `feat` or `fix` must include or update tests.
8. **Run `pytest` before push** — ensure nothing is broken.

## Continuous Integration (CI/CD)

The CI/CD pipeline is defined in three workflows:

- **`.github/workflows/ci.yml`** — runs tests (lint, type check, pytest) for every PR targeting `main` or `develop`, and after pushes to those branches.
- **`.github/workflows/delivery.yml`** — after CI passes, builds and publishes the Docker image to GHCR.
- **`.github/workflows/release.yml`** — when a maintainer creates a `v*` tag, builds, publishes and creates a GitHub Release.

| Workflow | Event | Action |
|----------|--------|------|
| **CI** | PR to `main` or `develop` | Tests (lint + type check + pytest) |
| **CI** | Push to `main` or `develop` | Tests (lint + type check + pytest) |
| **Delivery** | Push to `main` | Build, smoke test + push: `main`, `latest`, `sha-xxx` |
| **Delivery** | Push to `develop` | Build, smoke test + push: `develop`, `sha-xxx` |
| **Delivery** | Push to `release/**` | Build, smoke test + push: `release-x.y.z`, `sha-xxx` |
| **Release** | Tag `v*` created on git | Build, smoke test + push: `vX.Y.Z` + GitHub Release |

The following references are published to `ghcr.io/a11ydevs/acessilia-toolbox`:

| Tag | Source branch | Purpose |
|-----|-----------------|------------|
| `develop` | `develop` | Staging (updated via systemd timer) |
| `main` | `main` | Production (CD) |
| `latest` | `main` | Production (points to latest) |
| `sha-<commit>` | `main` or `develop` | Immutable reference |
| `vX.Y.Z` | Git tag `v*` | Official release |
| `release-x.y.z` | `release/*` | BHS |

## Pull Requests

1. Make sure your branch is up to date with `develop` (`git rebase origin/develop`).
2. Run `pytest tests/ -v` and verify all tests pass.
3. Clearly describe what the PR does and what problem it solves.
4. Reference related issues (e.g.: `Closes #42`).
5. Wait for review and adjust if needed.

## Questions?

Open an [issue](https://github.com/A11yDevs/acessilia-toolbox/issues) or start a [discussion](https://github.com/A11yDevs/acessilia-toolbox/discussions).