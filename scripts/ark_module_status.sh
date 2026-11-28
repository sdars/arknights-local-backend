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

echo "== module =="
adb -s "$ADB_SERIAL" shell "su -M -c 'cat /data/adb/ark-local/status 2>/dev/null; echo; cat /data/adb/ark-local/module.status 2>/dev/null'" || true
echo "== relay =="
adb -s "$ADB_SERIAL" shell "su -M -c 'ps -A -o PID,ARGS | grep \"[n]c -p 443\"'" || true
echo "== routes =="
adb -s "$ADB_SERIAL" shell "su -M -c 'iptables -t nat -S ARK_LOCAL 2>/dev/null | head -20'" || true
echo "== hosts =="
adb -s "$ADB_SERIAL" shell "su -M -c 'cat /system/etc/hosts'" || true
echo "== CA =="
adb -s "$ADB_SERIAL" shell "su -M -c 'ls -l /system/etc/security/cacerts/c8750f0d.0 2>/dev/null'" || true
echo "== adb reverse =="
adb -s "$ADB_SERIAL" reverse --list || true
