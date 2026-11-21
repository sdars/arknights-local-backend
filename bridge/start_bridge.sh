#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f ../.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source ../.env
  set +a
fi

: "${MITM_PORT:=8080}"
mkdir -p flows
exec /root/Knight/.venv/bin/mitmdump \
  --listen-host 127.0.0.1 \
  --listen-port "$MITM_PORT" \
  -s local_redirect.py \
  -w "flows/bridge-$(date +%Y%m%d-%H%M%S).mitm"

