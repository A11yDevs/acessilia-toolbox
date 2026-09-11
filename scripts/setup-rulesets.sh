#!/usr/bin/env bash
# setup-rulesets.sh — Apply or update GitHub rulesets via REST API.
#
# Usage:
#   ./scripts/setup-rulesets.sh              # Apply/update rulesets
#   DRY_RUN=1 ./scripts/setup-rulesets.sh     # Only show payload without sending
#
# Prerequisites:
#   - gh (GitHub CLI) authenticated with 'repo' scope
#   - jq installed
#
# Ruleset definitions:
#   .github/rulesets/*.json.example  — templates (rename to .json to activate)

set -euo pipefail
cd "$(dirname "$0")/.."

DRY_RUN="${DRY_RUN:-0}"

REPO="A11yDevs/acessilia-toolbox"
RULESETS_DIR=".github/rulesets"

if ! command -v jq &>/dev/null; then
  echo "❌ jq is required. Install with: brew install jq"
  exit 1
fi

if ! gh auth status &>/dev/null; then
  echo "❌ gh (GitHub CLI) not authenticated. Run: gh auth login"
  exit 1
fi

echo "=== Acessilia Toolbox — Ruleset Sync ==="
echo ""

for ruleset_file in "$RULESETS_DIR"/*.json; do
  [ -f "$ruleset_file" ] || continue

  name="$(jq -r '.name' "$ruleset_file")"
  echo "  Processing: $name ($(basename "$ruleset_file"))"

  if [ "$DRY_RUN" = "1" ]; then
    echo "  └─ DRY RUN — payload would be:"
    jq . "$ruleset_file"
    echo ""
    continue
  fi

  # Check if a ruleset with this name already exists
  EXISTING=$(gh api "/repos/$REPO/rulesets" --jq ".[] | select(.name == \"$name\") | .id" 2>/dev/null || echo "")

  if [ -n "$EXISTING" ]; then
    echo "  └─ Updating existing ruleset #$EXISTING..."
    gh api --method PATCH "/repos/$REPO/rulesets/$EXISTING" \
      --input "$ruleset_file" > /dev/null
    echo "  └─ ✅ Updated"
  else
    echo "  └─ Creating new ruleset..."
    gh api --method POST "/repos/$REPO/rulesets" \
      --input "$ruleset_file" > /dev/null
    echo "  └─ ✅ Created"
  fi
done

echo ""
echo "=== Done ==="