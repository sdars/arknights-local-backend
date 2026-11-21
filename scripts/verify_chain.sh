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
: "${ARK_TOKEN:=lab_manual_token}"
: "${ARK_UID:=10000000}"
: "${MYSQL_DATABASE:=arknights}"
: "${MYSQL_USER:=ark}"
: "${MYSQL_PASSWORD:=change-me-ark}"

code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

echo "server_time:        $(code "$SERVER_URL/general/v1/server_time")"
echo "network_config:     $(code "$SERVER_URL/api/remote_config/1/prod/default/Android/network_config")"
echo "signed_net_config:  $(code "$SERVER_URL/config/prod/official/network_config")"
echo "basic:              $(code "$SERVER_URL/user/info/v1/basic?token=${ARK_TOKEN}")"
echo "grant:              $(code -X POST -H 'Content-Type: application/json' -d "{\"token\":\"${ARK_TOKEN}\"}" "$SERVER_URL/user/oauth2/v2/grant")"
echo "account/login:      $(code -X POST -H 'Content-Type: application/json' -H 'uid: '"$ARK_UID" -d "{\"token\":\"${ARK_TOKEN}\",\"platform\":1,\"assetsVersion\":\"26-11-13-06-37-23-89286612\",\"clientVersion\":\"2.7.91\"}" "$SERVER_URL/account/login")"
echo "account/syncData:   $(code -X POST -H 'Content-Type: application/json' -H 'secret: '"$ARK_TOKEN" -H 'uid: '"$ARK_UID" -H 'seqnum: 1' -d '{}' "$SERVER_URL/account/syncData")"
echo "quest/battleStart:  $(code -X POST -H 'Content-Type: application/json' -H 'secret: '"$ARK_TOKEN" -H 'uid: '"$ARK_UID" -H 'seqnum: 2' -d '{"stageId":"guide_01","battleType":0,"startTs":1795241798,"isRetro":0,"pry":0,"usePracticeTicket":0,"isReplay":0}' "$SERVER_URL/quest/battleStart")"

