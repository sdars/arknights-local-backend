#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${MYSQL_DATABASE:=arknights}"
: "${MYSQL_USER:=ark}"
: "${MYSQL_PASSWORD:=change-me-ark}"
: "${ARK_PHONE:=13800138000}"
: "${ARK_TOKEN:=lab_manual_token}"
: "${ADB_SERIAL:=}"

echo "[1/2] seed MySQL account ${ARK_PHONE} token=${ARK_TOKEN}"
mysql -h127.0.0.1 -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE" <<SQL
-- 同一个 secret 只保留一行；历史上重复 seed 会生成多行，导致后端 queryAccountBySecret 返回 >1 而拒绝登录。
DELETE a FROM account a JOIN account b ON a.secret = b.secret AND a.uid > b.uid;
INSERT INTO account (phone, password, secret, user, mails, assistCharList, friend, ban)
SELECT '${ARK_PHONE}', MD5('ArkNightsLoongling001'), '${ARK_TOKEN}', '{}', '[]', '[]', '[]', 0
WHERE NOT EXISTS (SELECT 1 FROM account WHERE secret = '${ARK_TOKEN}');
UPDATE account
SET phone = '${ARK_PHONE}',
    password = MD5('ArkNightsLoongling001')
WHERE secret = '${ARK_TOKEN}';
SQL

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

