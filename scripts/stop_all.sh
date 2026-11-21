#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${ADB_SERIAL:=}"
: "${MITM_PORT:=8080}"
: "${SERVER_HTTP_PORT:=8443}"

echo "[1/3] stop mitmdump"
pkill -f 'mitmdump.*local_redirect.py' 2>/dev/null || true

echo "[2/3] stop Java backend"
pkill -f 'java -jar hypergryph.jar' 2>/dev/null || true

echo "[3/3] clear device proxy/reverse"
if [[ -n "$ADB_SERIAL" ]]; then
  adb -s "$ADB_SERIAL" reverse --remove "tcp:${MITM_PORT}" 2>/dev/null || true
  adb -s "$ADB_SERIAL" reverse --remove "tcp:${SERVER_HTTP_PORT}" 2>/dev/null || true
  adb -s "$ADB_SERIAL" shell settings put global http_proxy :0 2>/dev/null || true
fi

find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
echo "done (MySQL container kept; use 'docker compose down -v' to remove data)"

