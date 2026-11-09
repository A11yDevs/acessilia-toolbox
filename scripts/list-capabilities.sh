#!/usr/bin/env bash
# List every capability with the providers registered for it and their versions.
#
# The toolbox publishes capability versions in GET /v1/capabilities and provider
# versions in GET /v1/providers. This script joins both so an operator can see
# which provider will execute a capability and which version is deployed.
#
# Usage:
#   ./scripts/list-capabilities.sh
#   TOOLBOX_BASE_URL=https://toolbox.example TOOLBOX_API_KEY=... \
#     ./scripts/list-capabilities.sh
#   ./scripts/list-capabilities.sh --base https://toolbox.example --key ...
#   ./scripts/list-capabilities.sh --help
#
# Environment (a missing value is prompted for when stdin is a terminal):
#   TOOLBOX_BASE_URL  Base URL, without /v1. Also accepts BASE.
#   TOOLBOX_API_KEY   Bearer token. Also accepts KEY.
#
# Requires: curl, jq and mktemp.

set -euo pipefail

DEFAULT_BASE="https://www.acessilia3.inf.ufg.br/toolbox"
BASE="${TOOLBOX_BASE_URL:-${BASE:-}}"
KEY="${TOOLBOX_API_KEY:-${KEY:-}}"

usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

while (($#)); do
    case "$1" in
        -b | --base)
            [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 2; }
            BASE="$2"
            shift 2
            ;;
        -k | --key)
            [[ $# -ge 2 ]] || { echo "missing value for $1" >&2; exit 2; }
            KEY="$2"
            shift 2
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

missing=()
for command in curl jq mktemp; do
    command -v "$command" >/dev/null 2>&1 || missing+=("$command")
done
if ((${#missing[@]})); then
    printf 'missing required command(s): %s\n' "${missing[*]}" >&2
    exit 127
fi

if [[ -z "$BASE" ]]; then
    if [[ -t 0 ]]; then
        read -r -p "Toolbox base URL [$DEFAULT_BASE]: " BASE || true
    fi
    BASE="${BASE:-$DEFAULT_BASE}"
fi
BASE="${BASE%/}"
if [[ ! "$BASE" =~ ^https?:// ]]; then
    echo "base URL must start with http:// or https://: $BASE" >&2
    exit 2
fi

if [[ -z "$KEY" ]]; then
    if [[ -t 0 ]]; then
        read -r -s -p "Toolbox API key: " KEY || true
        printf '\n' >&2
    else
        echo "TOOLBOX_API_KEY is not set and stdin is not a terminal." >&2
        exit 1
    fi
fi
if [[ -z "$KEY" ]]; then
    echo "Toolbox API key is empty." >&2
    exit 1
fi
if [[ "$KEY" == *$'\n'* || "$KEY" == *$'\r'* ]]; then
    echo "Toolbox API key must not contain a line break." >&2
    exit 1
fi

umask 077
tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/toolbox-capabilities.XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

# Keep the token out of curl's argv (visible in the process list).
header_file="$tmpdir/headers"
printf 'Authorization: Bearer %s\n' "$KEY" >"$header_file"
chmod 600 "$header_file"

fetch() {
    local url="$1" out="$2" status
    if ! status="$(curl --silent --show-error --max-time 30 \
        --header "@$header_file" --output "$out" --write-out '%{http_code}' "$url")"; then
        echo "request failed: $url" >&2
        return 1
    fi
    if [[ "$status" != "200" ]]; then
        echo "HTTP $status from $url" >&2
        if [[ -s "$out" ]]; then
            head -c 500 "$out" >&2 || true
            printf '\n' >&2
        fi
        return 1
    fi
}

caps="$tmpdir/capabilities.json"
providers="$tmpdir/providers.json"
fetch "$BASE/v1/capabilities" "$caps"
fetch "$BASE/v1/providers" "$providers"

if ! jq -e . "$caps" >/dev/null || ! jq -e . "$providers" >/dev/null; then
    echo "response was not valid JSON" >&2
    exit 1
fi

printf 'capability\tcap_ver\tprovider\tprovider_ver\ttransport\tendpoint\n'
jq -s -r '
    (.[1] | map({key: .id, value: .}) | from_entries) as $providers
    | .[0][]
    | select(.latest)
    | . as $capability
    | ($capability.providers // [])[] as $provider_id
    | [
        $capability.id,
        $capability.version,
        $provider_id,
        ($providers[$provider_id].version // "-"),
        ($providers[$provider_id].transport // "-"),
        ($providers[$provider_id].endpoint // "-")
      ]
    | @tsv
' "$caps" "$providers"
