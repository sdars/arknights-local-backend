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
    <string name="USER_CACHE">[{"phone":"${ARK_PHONE}","token":"${ARK_TOKEN}","lastLoginTime":"$(date '+%Y/%m/%d %H:%M')","hgId":"1","deviceToken":"${DEVICE_TOKEN}"}]</string>
    <string name="DEVICE_CACHE">[{"hgId":"1","deviceToken":"${DEVICE_TOKEN}"}]</string>
    <string name="HypergryphUserProtocol">1</string>
    <boolean name="cache_account_times" value="true" />
</map>
XML

cat > tmp/ark-write-prefs.sh <<'SH'
#!/system/bin/sh
set -eu
APP_DIR=/data/data/com.hypergryph.arknights
PREFS="$APP_DIR/shared_prefs"
BACKUP=/sdcard/ark_backup
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP"
if [ -f "$PREFS/HypergryphSdkPreferences.xml" ]; then
  cp "$PREFS/HypergryphSdkPreferences.xml" "$BACKUP/HypergryphSdkPreferences.xml.$TS"
fi
mkdir -p "$PREFS"
cp /data/local/tmp/ark-sdk-prefs.xml "$PREFS/HypergryphSdkPreferences.xml"
APP_UID=$(stat -c %u "$APP_DIR")
APP_GID=$(stat -c %g "$APP_DIR")
chown "$APP_UID:$APP_GID" "$PREFS" "$PREFS/HypergryphSdkPreferences.xml"
chmod 771 "$PREFS"
chmod 660 "$PREFS/HypergryphSdkPreferences.xml"
restorecon -R "$PREFS" 2>/dev/null || true
ls -l "$PREFS/HypergryphSdkPreferences.xml"
SH

adb -s "$ADB_SERIAL" push tmp/ark-sdk-prefs.xml /data/local/tmp/ark-sdk-prefs.xml >/dev/null
adb -s "$ADB_SERIAL" push tmp/ark-write-prefs.sh /data/local/tmp/ark-write-prefs.sh >/dev/null
adb -s "$ADB_SERIAL" shell "su -M -c 'sh /data/local/tmp/ark-write-prefs.sh'"
echo "done; force-stop the game before next launch: adb -s ${ADB_SERIAL} shell am force-stop com.hypergryph.arknights"
