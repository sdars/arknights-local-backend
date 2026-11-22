#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

: "${ADB_SERIAL:=}"
: "${SERVER_HTTP_PORT:=8443}"
: "${MITM_REVERSE_PORT:=4443}"
: "${DEVICE_SDK_PORT:=8444}"

echo "[1/4] stop watchdog / systemd services"
for unit in ark-watchdog.service ark-mitm.service ark-backend.service; do
  if systemctl is-active --quiet "$unit" 2>/dev/null; then
    systemctl stop "$unit" 2>/dev/null || true
  fi
done
pkill -f 'scripts/watchdog.sh' 2>/dev/null || true

echo "[2/4] stop mitmdump TLS reverse"
pkill -f 'mitmdump.*reverse:http://127.0.0.1:8443' 2>/dev/null || true

echo "[3/4] stop python backend"
pkill -f '/root/Knight/.venv/bin/python run.py' 2>/dev/null || true

echo "[4/4] clear device proxy/reverse/forward"
if [[ -n "$ADB_SERIAL" ]]; then
  adb -s "$ADB_SERIAL" reverse --remove "tcp:${SERVER_HTTP_PORT}" 2>/dev/null || true
  adb -s "$ADB_SERIAL" reverse --remove "tcp:${DEVICE_SDK_PORT}" 2>/dev/null || true
  adb -s "$ADB_SERIAL" shell settings put global http_proxy :0 2>/dev/null || true
  adb -s "$ADB_SERIAL" shell "su -M -c 'pkill -f \"nc -p 443 -L nc 127.0.0.1 8444\"'" 2>/dev/null || true
fi

find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
echo "done"
