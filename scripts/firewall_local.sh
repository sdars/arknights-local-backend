#!/usr/bin/env bash
set -euo pipefail

# 8443 按当前部署要求对公网开放；36650 仍只允许本机。
for port in 36650; do
  if ! iptables -C INPUT -p tcp --dport "$port" ! -s 127.0.0.1/32 -j DROP 2>/dev/null; then
    iptables -I INPUT -p tcp --dport "$port" ! -s 127.0.0.1/32 -j DROP
  fi
done

iptables -S INPUT | grep -E '8443|36650' || true
echo "ok: 8443 public; 36650 local-only"

