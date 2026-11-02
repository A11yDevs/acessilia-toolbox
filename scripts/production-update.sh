#!/usr/bin/env bash
# Manual, digest-pinned production deployment. No branch tracking or timer.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mode="${1:---check}"
directory="${PRODUCTION_DIR:-/opt/acessilia/toolbox}"

case "$mode" in
  --check|--apply) ;;
  *) echo "Usage: PRODUCTION_DIR=/opt/acessilia/toolbox $0 [--check|--apply]" >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { echo "Unexpected arguments" >&2; exit 2; }

# The helper validates before pulling or replacing any production service.
exec python3 "$SCRIPT_DIR/production_deploy.py" "${mode#--}" --directory "$directory"
