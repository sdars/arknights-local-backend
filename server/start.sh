#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f ../.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source ../.env
  set +a
fi

: "${SERVER_URL:=http://127.0.0.1:8443}"
: "${SERVER_HTTP_PORT:=8443}"
: "${SERVER_HTTPS_PORT:=36650}"
: "${MYSQL_DATABASE:=arknights}"
: "${MYSQL_USER:=ark}"
: "${MYSQL_PASSWORD:=change-me-ark}"

export SERVER_URL SERVER_HTTP_PORT SERVER_HTTPS_PORT MYSQL_DATABASE MYSQL_USER MYSQL_PASSWORD

python3 - <<'PY'
import os
tpl = open('config.example.json', encoding='utf-8').read()
for key in ('SERVER_URL', 'SERVER_HTTP_PORT', 'SERVER_HTTPS_PORT',
            'MYSQL_DATABASE', 'MYSQL_USER', 'MYSQL_PASSWORD'):
    tpl = tpl.replace('${' + key + '}', os.environ[key])
open('config.json', 'w', encoding='utf-8').write(tpl)
print('generated config.json')
PY

exec java -jar hypergryph.jar --server.address=127.0.0.1


