#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${ADB_SERIAL:?ADB_SERIAL required}"
: "${SERVER_HTTP_PORT:=8443}"
: "${MITM_REVERSE_PORT:=4443}"
: "${DEVICE_SDK_PORT:=8444}"
: "${DEVICE_HTTPS_PORT:=443}"

if [[ "$(timeout 10 adb -s "$ADB_SERIAL" get-state 2>/dev/null | tr -d '\r' || true)" != "device" ]]; then
  timeout 10 adb disconnect "$ADB_SERIAL" >/dev/null 2>&1 || true
  timeout 15 adb connect "$ADB_SERIAL" >/dev/null 2>&1 || true
  sleep 2
fi

adb -s "$ADB_SERIAL" reverse "tcp:${SERVER_HTTP_PORT}" "tcp:${SERVER_HTTP_PORT}"
adb -s "$ADB_SERIAL" reverse "tcp:${DEVICE_SDK_PORT}" "tcp:${MITM_REVERSE_PORT}"
adb -s "$ADB_SERIAL" shell settings put global http_proxy :0

HOSTS=(as.hypergryph.com game-config.hypergryph.com ak-conf.hypergryph.com launcher.hypergryph.com ak-webview.hypergryph.com)
HOSTS_SCRIPT="for h in ${HOSTS[*]}; do grep -q \"^127\\.0\\.0\\.1 \$h\$\" /system/etc/hosts || echo \"127.0.0.1 \$h\" >> /system/etc/hosts; done"
adb -s "$ADB_SERIAL" shell "su -M -c '$HOSTS_SCRIPT'" >/dev/null 2>&1 || true

NC_SCRIPT="if ! ps -A -o ARGS | grep -q '[n]c -p ${DEVICE_HTTPS_PORT} -L nc 127.0.0.1 ${DEVICE_SDK_PORT}'; then nohup nc -p ${DEVICE_HTTPS_PORT} -L nc 127.0.0.1 ${DEVICE_SDK_PORT} >/dev/null 2>&1 & fi"
adb -s "$ADB_SERIAL" shell "su -M -c '$NC_SCRIPT'" >/dev/null 2>&1 || true

echo "reverse:"
adb -s "$ADB_SERIAL" reverse --list
echo "proxy: $(adb -s "$ADB_SERIAL" shell settings get global http_proxy | tr -d '\r')"
echo "hosts:"
adb -s "$ADB_SERIAL" shell 'grep -v "^#" /system/etc/hosts | tail -8'
echo "tls forward: $(adb -s "$ADB_SERIAL" shell 'ps -A -o ARGS | grep "[n]c -p 443" | head -1' | tr -d '\r')"

