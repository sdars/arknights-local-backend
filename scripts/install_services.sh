#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

install -m 0644 deploy/ark-backend.service /etc/systemd/system/ark-backend.service
install -m 0644 deploy/ark-mitm.service /etc/systemd/system/ark-mitm.service
install -m 0644 deploy/ark-watchdog.service /etc/systemd/system/ark-watchdog.service
chmod +x scripts/watchdog.sh scripts/connect_device.sh scripts/healthcheck.sh scripts/stop_all.sh scripts/seed_account.sh

# Avoid duplicate manual processes fighting the units for 8443/4443.
pkill -f '/root/Knight/.venv/bin/python run.py' 2>/dev/null || true
pkill -f 'mitmdump.*reverse:http://127.0.0.1:8443' 2>/dev/null || true
sleep 1

systemctl daemon-reload
systemctl enable --now ark-backend.service ark-mitm.service ark-watchdog.service

for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf -m 3 http://127.0.0.1:8443/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

systemctl --no-pager --full status ark-backend ark-mitm ark-watchdog | sed -n '1,80p'
