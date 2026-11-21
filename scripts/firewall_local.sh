#!/usr/bin/env bash
set -euo pipefail

# 只允许本机访问 Java 后端端口；设备通过 adb reverse 从本机回环进入。
for port in 8443 36650; do
  if ! iptables -C INPUT -p tcp --dport "$port" ! -s 127.0.0.1/32 -j DROP 2>/dev/null; then
    iptables -I INPUT -p tcp --dport "$port" ! -s 127.0.0.1/32 -j DROP
  fi
done

iptables -S INPUT | grep -E '8443|36650' || true
echo "ok: 8443/36650 only accept 127.0.0.1"

