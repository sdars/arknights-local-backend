#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

: "${SERVER_URL:=http://127.0.0.1:8443}"
: "${ADB_SERIAL:=}"
: "${SERVER_HTTP_PORT:=8443}"
: "${MITM_REVERSE_PORT:=4443}"
: "${DEVICE_SDK_PORT:=8444}"
: "${DEVICE_HTTPS_PORT:=443}"

fail=0
check_http() {
  local name="$1" url="$2"
  local code
  code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "$url" || true)
  if [[ "$code" == "200" ]]; then
    echo "OK   $name ($code)"
  else
    echo "FAIL $name (${code:-no-response})"
    fail=1
  fi
}

check_http "healthz" "$SERVER_URL/healthz"
check_http "server_time" "$SERVER_URL/general/v1/server_time"
check_http "network_config" "$SERVER_URL/api/remote_config/1/prod/default/Android/network_config"
check_http "webui_login" "$SERVER_URL/admin/login"

if ps -eo args | grep -q '[p]ython run.py'; then
  echo "OK   python backend process"
else
  echo "FAIL python backend process"
  fail=1
fi

if pgrep -f 'mitmdump.*reverse:http://127.0.0.1:8443.*listen-port 4443' >/dev/null 2>&1; then
  echo "OK   mitm TLS reverse process"
else
  echo "FAIL mitm TLS reverse process"
  fail=1
fi

if [[ -n "$ADB_SERIAL" ]]; then
  if adb -s "$ADB_SERIAL" get-state 2>/dev/null | grep -q device; then
    echo "OK   adb device $ADB_SERIAL"
  else
    echo "FAIL adb device $ADB_SERIAL"
    fail=1
  fi
  if adb -s "$ADB_SERIAL" reverse --list 2>/dev/null | grep -q "tcp:${SERVER_HTTP_PORT} tcp:${SERVER_HTTP_PORT}"; then
    echo "OK   adb reverse ${SERVER_HTTP_PORT}"
  else
    echo "FAIL adb reverse ${SERVER_HTTP_PORT}"
    fail=1
  fi
  if adb -s "$ADB_SERIAL" reverse --list 2>/dev/null | grep -q "tcp:${DEVICE_SDK_PORT} tcp:${MITM_REVERSE_PORT}"; then
    echo "OK   adb reverse ${DEVICE_SDK_PORT} -> ${MITM_REVERSE_PORT}"
  else
    echo "FAIL adb reverse ${DEVICE_SDK_PORT} -> ${MITM_REVERSE_PORT}"
    fail=1
  fi
  proxy=$(adb -s "$ADB_SERIAL" shell settings get global http_proxy 2>/dev/null | tr -d '\r' || true)
  if [[ "$proxy" == ":0" || "$proxy" == "null" ]]; then
    echo "OK   device proxy disabled"
  else
    echo "FAIL device proxy is '$proxy'"
    fail=1
  fi
  if adb -s "$ADB_SERIAL" shell 'ps -A -o ARGS | grep -q "[n]c -p 443 -L nc 127.0.0.1 8444"' >/dev/null 2>&1; then
    echo "OK   device 443 -> 8444 forward"
  else
    echo "FAIL device 443 -> 8444 forward"
    fail=1
  fi
  if adb -s "$ADB_SERIAL" shell 'grep -q "^127.0.0.1 as.hypergryph.com$" /system/etc/hosts' >/dev/null 2>&1; then
    echo "OK   device hosts mapping"
  else
    echo "FAIL device hosts mapping"
    fail=1
  fi
fi

exit "$fail"
