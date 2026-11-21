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
: "${MITM_PORT:=8080}"
: "${SERVER_HTTP_PORT:=8443}"

adb -s "$ADB_SERIAL" reverse "tcp:${MITM_PORT}" "tcp:${MITM_PORT}"
adb -s "$ADB_SERIAL" reverse "tcp:${SERVER_HTTP_PORT}" "tcp:${SERVER_HTTP_PORT}"
adb -s "$ADB_SERIAL" shell settings put global http_proxy "127.0.0.1:${MITM_PORT}"

echo "reverse:"
adb -s "$ADB_SERIAL" reverse --list
echo "proxy: $(adb -s "$ADB_SERIAL" shell settings get global http_proxy | tr -d '\r')"

if ! adb -s "$ADB_SERIAL" shell 'ls /system/etc/security/cacerts/c8750f0d.0' >/dev/null 2>&1; then
  cat <<'EOF'
[!] mitmproxy CA 未在系统 CA 目录中找到。
    需要把 ~/.mitmproxy/mitmproxy-ca-cert.pem 转成 PEM 哈希文件名并放入
    /system/etc/security/cacerts（root，bind mount 或 overlay），然后重启设备。
    当前环境已通过 /data/local/tmp/cacerts -> /system/etc/security/cacerts 的 bind mount 注入。
EOF
fi

