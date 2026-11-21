#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${ARK_PYTHON:-/root/Knight/.venv/bin/python}"
: "${ARK_PHONE:=13800138000}"
: "${ARK_TOKEN:=lab_manual_token}"
: "${ARK_NICKNAME:=博士}"
: "${ADB_SERIAL:=}"

echo "[1/2] seed SQLite account phone=${ARK_PHONE} token=${ARK_TOKEN}"
cd backend
ARK_PHONE="$ARK_PHONE" ARK_TOKEN="$ARK_TOKEN" ARK_NICKNAME="$ARK_NICKNAME" "$PYTHON" - <<'PY'
import os
import sys

sys.path.insert(0, ".")
from app.services import create_account

account, created = create_account(
    os.environ["ARK_PHONE"],
    os.environ["ARK_TOKEN"],
    os.environ.get("ARK_NICKNAME", "博士"),
)
print("uid=%s created=%s" % (account["uid"], created))
PY
cd ..

if [[ -z "$ADB_SERIAL" ]]; then
  echo "ADB_SERIAL 为空，跳过设备登录缓存写入"
  exit 0
fi

echo "[2/2] write HypergryphSdkPreferences.xml on ${ADB_SERIAL}"
mkdir -p tmp
DEVICE_TOKEN="dev_${ARK_TOKEN}"
cat > tmp/ark-sdk-prefs.xml <<XML
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <boolean name="IS_AUTO_LOGIN" value="true" />
    <string name="USER_CACHE">[{"phone":"${ARK_PHONE}","token":"${ARK_TOKEN}","lastLoginTime":"2026-01-01 00:00","hgId":"1","deviceToken":"${DEVICE_TOKEN}"}]</string>
    <string name="DEVICE_CACHE">[{"hgId":"1","deviceToken":"${DEVICE_TOKEN}"}]</string>
    <string name="HypergryphUserProtocol">1</string>
    <boolean name="cache_account_times" value="true" />
</map>
XML

adb -s "$ADB_SERIAL" push tmp/ark-sdk-prefs.xml /sdcard/ark-sdk-prefs.xml >/dev/null
adb -s "$ADB_SERIAL" shell "su -c 'APP_UID=\$(stat -c %u /data/data/com.hypergryph.arknights); APP_GID=\$(stat -c %g /data/data/com.hypergryph.arknights); cp /sdcard/ark-sdk-prefs.xml /data/data/com.hypergryph.arknights/shared_prefs/HypergryphSdkPreferences.xml; chown \$APP_UID:\$APP_GID /data/data/com.hypergryph.arknights/shared_prefs/HypergryphSdkPreferences.xml; chmod 660 /data/data/com.hypergryph.arknights/shared_prefs/HypergryphSdkPreferences.xml; restorecon /data/data/com.hypergryph.arknights/shared_prefs/HypergryphSdkPreferences.xml'"
echo "done; force-stop the game before next launch: adb -s ${ADB_SERIAL} shell am force-stop com.hypergryph.arknights"
