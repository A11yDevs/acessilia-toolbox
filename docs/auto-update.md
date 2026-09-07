# Automatic Image Update (Auto-Update)

The Acessilia Toolbox offers automatic staging container updates via **systemd user timer**. Whenever a new commit is merged into `develop` (or the tracked branch), the container is automatically updated within 5 minutes.

## How it works

```
[Git push] → [GitHub Actions: CI + Delivery] → [GHCR: nova imagem develop]
                                                      ↑
[systemd timer (5 min)] → [staging-update.sh] ────────┘
                              │
                              ├── 1. Consulta GitHub API (SHA do último commit)
                              ├── 2. Confirma que a imagem sha-<7> já foi publicada
                              └── 3. docker pull + docker compose up -d
```

## Installation

### Full setup (recommended)

Run the interactive setup script:

```bash
./scripts/setup-homologacao.sh
```

Or non-interactively:

```bash
GITHUB_USER=your-user GHCR_TOKEN=your-token ./scripts/setup-homologacao.sh
```

The script:

1. Checks dependencies (Docker, Compose, jq)
2. Configures GHCR authentication
3. Creates `.env` from `.env.example` if it doesn't exist
4. Starts containers with `docker-compose.staging.yml`
5. Installs the systemd user timer

### Manual installation

If you prefer to install manually:

```bash
# 1. Authenticate to GHCR
echo <token> | docker login ghcr.io -u <your-user> --password-stdin

# 2. Copy the update script
sudo mkdir -p /opt/acessilia-toolbox/scripts
sudo cp scripts/staging-update.sh /opt/acessilia-toolbox/scripts/
sudo chmod +x /opt/acessilia-toolbox/scripts/staging-update.sh

# 3. Create the service unit
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/staging-update.service << 'SERVICE'
[Unit]
Description=Update acessilia-toolbox staging container

[Service]
Type=oneshot
ExecStart=/opt/acessilia-toolbox/scripts/staging-update.sh
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
SERVICE

# 4. Create the timer unit (every 5 minutes)
cat > ~/.config/systemd/user/staging-update.timer << 'TIMER'
[Unit]
Description=Check acessilia-toolbox staging updates every 5 minutes

[Timer]
OnBootSec=30s
OnUnitActiveSec=300s

[Install]
WantedBy=timers.target
TIMER

# 5. Enable the timer
systemctl --user daemon-reload
systemctl --user enable --now staging-update.timer
```

## Management

### Check timer status

```bash
systemctl --user status staging-update.timer
systemctl --user list-timers
```

### View last execution logs

```bash
systemctl --user status staging-update.service
journalctl --user -u staging-update.service -n 50
```

### Run a manual update

```bash
./scripts/staging-update.sh
```

### Stop the timer

```bash
systemctl --user stop staging-update.timer
systemctl --user disable staging-update.timer
```

### Check which image is running

```bash
docker inspect acessilia-toolbox-staging | jq '.[0].Config.Image'
```

## Branch tracking (BHS)

During the Bug Hunting/Squashing (BHS) cycle, you can point staging to a specific release branch:

```bash
# In the staging server's .env
echo "TRACK_BRANCH=release/0.1.0" >> .env

# Or via environment variable
TRACK_BRANCH=release/0.1.0 ./scripts/staging-update.sh
```

To go back to tracking `develop`, remove the variable from `.env`:

```bash
sed -i '/^TRACK_BRANCH=/d' .env
```

## Status files

The script maintains status files in `var/data/`:

- `var/data/.last_sha` — SHA of the last commit that triggered an update
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
2. Check logs: `journalctl --user -u staging-update.service -n 50`
3. Run manually: `./scripts/staging-update.sh`
4. Check if the image was published: `docker manifest inspect ghcr.io/a11ydevs/acessilia-toolbox:sha-<sha7>`

### docker compose not found

The script uses `docker compose` (plugin). If your environment uses `docker-compose` (standalone), set:

```bash
export COMPOSE_FILE="docker-compose.staging.yml"
docker-compose up -d
```

### Permission denied accessing /opt/acessilia-toolbox

The script needs `sudo` to create directories in `/opt`. Run the setup with a sudo-enabled user.

### Linger not enabled

The user timer won't survive logout if linger is not active. The setup script enables it automatically, but you can check with:

```bash
loginctl show-user $USER | grep Linger
```