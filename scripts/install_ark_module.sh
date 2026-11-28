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
MODULE_ID=ark-local
ZIP=${ARK_MODULE_ZIP:-$PWD/tmp/ark-local-module.zip}
ACTIVATE=0
INSTALL_MODE=${ARK_INSTALL_MODE:-auto}
for arg in "$@"; do
  case "$arg" in
    --activate) ACTIVATE=1 ;;
    --reboot) ACTIVATE=2 ;;
    --apd-install) INSTALL_MODE=apd ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

"$PWD/scripts/build_ark_module.sh" >/dev/null

if [[ "$(timeout 10 adb -s "$ADB_SERIAL" get-state 2>/dev/null | tr -d '\r' || true)" != "device" ]]; then
  timeout 10 adb connect "$ADB_SERIAL" >/dev/null 2>&1 || true
  sleep 2
fi

adb -s "$ADB_SERIAL" push "$ZIP" /data/local/tmp/ark-local-module.zip >/dev/null

if [[ "$INSTALL_MODE" == "apd" ]]; then
  echo "installing via APatch apd..."
  adb -s "$ADB_SERIAL" shell "su -M -c '/data/adb/apd module install /data/local/tmp/ark-local-module.zip'"
elif [[ "$INSTALL_MODE" == "auto" ]] && adb -s "$ADB_SERIAL" shell 'su -M -c "command -v magisk"' 2>/dev/null | grep -q magisk; then
  echo "installing via Magisk..."
  adb -s "$ADB_SERIAL" shell "su -M -c 'magisk --install-module /data/local/tmp/ark-local-module.zip'"
else
  # APatch's CLI installer can block on devices whose /proc/mounts read stalls.
  # The module directory format is identical, so install by directory copy and
  # let APatch run the hooks on the next boot (or via `apd post-fs-data`).
  echo "installing module directory (APatch/KernelSU compatible)..."
  TMP=$(mktemp -d)
  unzip -q "$ZIP" -d "$TMP/$MODULE_ID"
  adb -s "$ADB_SERIAL" shell "su -M -c 'rm -rf /data/local/tmp/ark-local-module'" >/dev/null 2>&1 || true
  adb -s "$ADB_SERIAL" push "$TMP/$MODULE_ID" /data/local/tmp/ark-local-module >/dev/null
  adb -s "$ADB_SERIAL" shell "su -M -c 'while umount -l /system/etc/hosts 2>/dev/null; do :; done; while umount -l /system/etc/security/cacerts 2>/dev/null; do :; done; rm -rf /data/adb/modules/ark-local; cp -a /data/local/tmp/ark-local-module /data/adb/modules/ark-local; find /data/adb/modules/ark-local -type f -exec chmod 644 {} \; ; find /data/adb/modules/ark-local -name \"*.sh\" -exec chmod 755 {} \;'"
  rm -rf "$TMP"
fi

adb -s "$ADB_SERIAL" reverse tcp:8443 tcp:8443
adb -s "$ADB_SERIAL" reverse tcp:8444 tcp:4443

if [[ "$ACTIVATE" -ge 1 ]]; then
  # Execute the hooks directly.  APatch's `post-fs-data` command runs its own
  # mount scan on some builds, which can block on devices with a wedged
  # /proc/mounts; the module hooks themselves are idempotent.
  adb -s "$ADB_SERIAL" shell "su -M -c 'sh /data/adb/modules/ark-local/post-fs-data.sh'" || true
  adb -s "$ADB_SERIAL" shell "su -M -c 'nohup sh /data/adb/modules/ark-local/service.sh </dev/null >/data/adb/ark-local/install.log 2>&1 &'" || true
  sleep 1
fi

"$PWD/scripts/ark_module_status.sh" || true

if [[ "$ACTIVATE" -eq 2 ]]; then
  echo "rebooting device..."
  adb -s "$ADB_SERIAL" reboot || true
else
  echo "installed. module scripts also run automatically on the next boot."
fi
