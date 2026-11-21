#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

: "${ADB_SERIAL:=}"
: "${MITM_PORT:=8080}"
: "${SERVER_HTTP_PORT:=8443}"

echo "[1/3] stop mitmdump"
pkill -f 'mitmdump.*local_redirect.py' 2>/dev/null || true

echo "[2/3] stop python backend"
pkill -f 'python run.py' 2>/dev/null || true

echo "[3/3] clear device proxy/reverse"
if [[ -n "$ADB_SERIAL" ]]; then
  adb -s "$ADB_SERIAL" reverse --remove "tcp:${MITM_PORT}" 2>/dev/null || true
  adb -s "$ADB_SERIAL" reverse --remove "tcp:${SERVER_HTTP_PORT}" 2>/dev/null || true
  adb -s "$ADB_SERIAL" shell settings put global http_proxy :0 2>/dev/null || true
fi

find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
echo "done"
