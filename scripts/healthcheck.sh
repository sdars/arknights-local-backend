#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${SERVER_URL:=http://127.0.0.1:8443}"
: "${ADB_SERIAL:=}"
: "${MITM_PORT:=8080}"
: "${SERVER_HTTP_PORT:=8443}"

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

check_http "server_time" "$SERVER_URL/general/v1/server_time"
check_http "network_config" "$SERVER_URL/api/remote_config/1/prod/default/Android/network_config"

if docker ps --filter name=arkmysql --format '{{.Status}}' | grep -q '^Up'; then
  echo "OK   mysql container"
else
  echo "FAIL mysql container"
  fail=1
fi

if ps -eo args | grep -q '[h]ypergryph.jar'; then
  echo "OK   java backend process"
else
  echo "FAIL java backend process"
  fail=1
fi

if ps -eo args | grep -q '[m]itmdump.*local_redirect.py'; then
  echo "OK   mitm bridge process"
else
  echo "FAIL mitm bridge process"
  fail=1
fi

if [[ -n "$ADB_SERIAL" ]]; then
  if adb -s "$ADB_SERIAL" get-state 2>/dev/null | grep -q device; then
    echo "OK   adb device $ADB_SERIAL"
  else
    echo "FAIL adb device $ADB_SERIAL"
    fail=1
  fi
  if adb -s "$ADB_SERIAL" reverse --list | grep -q "tcp:${SERVER_HTTP_PORT}"; then
    echo "OK   adb reverse ${SERVER_HTTP_PORT}"
  else
    echo "FAIL adb reverse ${SERVER_HTTP_PORT}"
    fail=1
  fi
  if adb -s "$ADB_SERIAL" reverse --list | grep -q "tcp:${MITM_PORT}"; then
    echo "OK   adb reverse ${MITM_PORT}"
  else
    echo "FAIL adb reverse ${MITM_PORT}"
    fail=1
  fi
fi

exit "$fail"

