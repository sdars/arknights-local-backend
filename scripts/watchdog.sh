#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${ADB_SERIAL:?ADB_SERIAL required (for example 8.162.0.198:30135)}"
: "${SERVER_HTTP_PORT:=8443}"
: "${MITM_REVERSE_PORT:=4443}"
: "${DEVICE_SDK_PORT:=8444}"
: "${DEVICE_HTTPS_PORT:=443}"
: "${ARK_WATCHDOG_INTERVAL:=15}"
: "${ARK_WATCHDOG_LOG:=logs/watchdog.log}"

mkdir -p logs bridge/flows
LOG_FILE="$ARK_WATCHDOG_LOG"

log() {
  local line
  line="[$(date '+%F %T')] $*"
  printf '%s\n' "$line" | tee -a "$LOG_FILE" >/dev/null
}

rotate_log() {
  if [[ -f "$LOG_FILE" ]] && [[ $(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0) -gt 5242880 ]]; then
    mv -f "$LOG_FILE" "${LOG_FILE}.1"
  fi
}

ensure_disk_space() {
  local used
  used=$(df -P / | awk 'NR == 2 { gsub(/%/, "", $5); print $5 }')
  if [[ "$used" =~ ^[0-9]+$ ]] && (( used >= 95 )); then
    log "disk usage ${used}%, vacuuming journals and expired thumbnails"
    journalctl --vacuum-size=512M >/dev/null 2>&1 || true
    find backend/data/gallery_thumbnails -type f -mtime +30 -delete 2>/dev/null || true
    used=$(df -P / | awk 'NR == 2 { gsub(/%/, "", $5); print $5 }')
    log "disk usage after maintenance: ${used}%"
  fi
}

adb_state() {
  timeout 10 adb -s "$ADB_SERIAL" get-state 2>/dev/null | tr -d '\r' || true
}

ensure_adb() {
  if [[ "$(adb_state)" == "device" ]]; then
    return 0
  fi
  log "ADB $ADB_SERIAL not ready, reconnecting"
  timeout 10 adb disconnect "$ADB_SERIAL" >/dev/null 2>&1 || true
  timeout 15 adb connect "$ADB_SERIAL" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5; do
    [[ "$(adb_state)" == "device" ]] && return 0
    sleep 2
  done
  log "ERROR: ADB $ADB_SERIAL still unavailable"
  return 1
}

ensure_reverse() {
  local local_port="$1" remote_port="$2"
  if timeout 10 adb -s "$ADB_SERIAL" reverse --list 2>/dev/null | grep -q "tcp:${local_port} tcp:${remote_port}"; then
    return 0
  fi
  log "restore adb reverse tcp:${local_port} -> tcp:${remote_port}"
  timeout 10 adb -s "$ADB_SERIAL" reverse "tcp:${local_port}" "tcp:${remote_port}" >/dev/null
}

ensure_proxy_off() {
  local proxy
  proxy=$(timeout 10 adb -s "$ADB_SERIAL" shell settings get global http_proxy 2>/dev/null | tr -d '\r' || true)
  if [[ "$proxy" != ":0" && "$proxy" != "null" && -n "$proxy" ]]; then
    log "disable system proxy (was '$proxy')"
    timeout 10 adb -s "$ADB_SERIAL" shell settings put global http_proxy :0 >/dev/null
  fi
}

ensure_hosts() {
  local hosts=(openapi.alipay.com as.hypergryph.com game-config.hypergryph.com ak-conf.hypergryph.com launcher.hypergryph.com ak-webview.hypergryph.com)
  local module_hosts=/data/adb/modules/ark-local/system/etc/hosts
  local script
  if timeout 10 adb -s "$ADB_SERIAL" shell "su -M -c '[ -f $module_hosts ]'" >/dev/null 2>&1; then
    # The module owns a system_file-context hosts file; netd can read it,
    # unlike /data/local/tmp/hosts.ark which is shell_data_file.
    script="src=\$(stat -c '%d:%i' $module_hosts 2>/dev/null); dst=\$(stat -c '%d:%i' /system/etc/hosts 2>/dev/null); if [ \"\$src\" != \"\$dst\" ]; then chcon u:object_r:system_file:s0 $module_hosts 2>/dev/null || true; mount --bind $module_hosts /system/etc/hosts 2>/dev/null || true; fi; grep -q 'openapi.alipay.com' /system/etc/hosts"
  else
    # /system is normally read-only. Keep a writable copy and bind-mount it over
    # /system/etc/hosts so the mapping survives package restarts and watchdogs.
    script="[ -f /data/local/tmp/hosts.ark ] || cp /system/etc/hosts /data/local/tmp/hosts.ark 2>/dev/null || true; for h in ${hosts[*]}; do grep -q \"^127\\.0\\.0\\.1 \$h\$\" /data/local/tmp/hosts.ark || echo \"127.0.0.1 \$h\" >> /data/local/tmp/hosts.ark; done; src=\$(stat -c '%d:%i' /data/local/tmp/hosts.ark 2>/dev/null); dst=\$(stat -c '%d:%i' /system/etc/hosts 2>/dev/null); if [ \"\$src\" != \"\$dst\" ]; then mount --bind /data/local/tmp/hosts.ark /system/etc/hosts 2>/dev/null || true; fi; grep -q 'openapi.alipay.com' /system/etc/hosts"
  fi
  if ! timeout 15 adb -s "$ADB_SERIAL" shell "su -M -c '$script'" >/dev/null 2>&1; then
    log "WARN: could not ensure device hosts mapping (root/mount may be unavailable)"
  fi
}

ensure_https_forward() {
  local cmd="if ! ps -A -o ARGS | grep -q '[n]c -p ${DEVICE_HTTPS_PORT} -L nc 127.0.0.1 ${DEVICE_SDK_PORT}'; then nohup nc -p ${DEVICE_HTTPS_PORT} -L nc 127.0.0.1 ${DEVICE_SDK_PORT} >/dev/null 2>&1 & fi"
  if ! timeout 15 adb -s "$ADB_SERIAL" shell "su -M -c '$cmd'" >/dev/null 2>&1; then
    log "WARN: could not ensure device ${DEVICE_HTTPS_PORT} -> ${DEVICE_SDK_PORT} forward"
  fi
}

ensure_pay_routes() {
  local pkg="com.hypergryph.arknights"
  local uid
  uid=$(timeout 10 adb -s "$ADB_SERIAL" shell "dumpsys package $pkg | grep -m1 'userId='" 2>/dev/null | tr -dc '0-9' || true)
  if [[ -z "$uid" ]]; then
    log "WARN: could not resolve $pkg uid for payment routes"
    return 0
  fi
  # Keep Hypergryph SDK, launcher, WebView and Alipay page-pay on the local
  # mitm path. Hosts alone is not enough because Android netd caches DNS and
  # the Java SDK / WebView use their own resolver state. Use an app-UID scoped
  # chain so unrelated apps and the resource CDN keep their normal routing.
  local domains=(as.hypergryph.com game-config.hypergryph.com ak-conf.hypergryph.com launcher.hypergryph.com ak-webview.hypergryph.com openapi.alipay.com)
  local routes=(110.75.170.0/24 110.75.231.202/32 110.75.236.202/32 110.75.244.202/32 110.76.18.202/32 43.145.18.0/24 43.174.224.0/23)
  local domain line ip
  for domain in "${domains[@]}"; do
    line=$(timeout 10 adb -s "$ADB_SERIAL" shell "ping -c 1 -W 1 ${domain}" 2>/dev/null | head -1 || true)
    ip=$(printf '%s' "$line" | grep -oE '\([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\)' | head -1 | tr -d '()')
    if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && [[ "$ip" != 127.* ]]; then
      routes+=("${ip}/32")
    fi
  done
  local script="iptables -t nat -N ARK_LOCAL 2>/dev/null || true; iptables -t nat -F ARK_LOCAL; "
  local route
  while IFS= read -r route; do
    script+="iptables -t nat -A ARK_LOCAL -d ${route} -p tcp --dport 443 -j DNAT --to-destination 127.0.0.1:${DEVICE_SDK_PORT}; "
  done < <(printf '%s\n' "${routes[@]}" | sort -u)
  script+="iptables -t nat -C OUTPUT -m owner --uid-owner ${uid} -p tcp --dport 443 -j ARK_LOCAL 2>/dev/null || iptables -t nat -I OUTPUT 1 -m owner --uid-owner ${uid} -p tcp --dport 443 -j ARK_LOCAL; "
  if ! timeout 20 adb -s "$ADB_SERIAL" shell "su -M -c '$script'" >/dev/null 2>&1; then
    log "WARN: could not ensure app scoped local TLS routes"
  fi
}

ensure_backend() {
  if curl -sf -m 3 "http://127.0.0.1:${SERVER_HTTP_PORT}/healthz" >/dev/null 2>&1; then
    return 0
  fi
  log "backend unhealthy, restarting"
  if systemctl list-unit-files ark-backend.service 2>/dev/null | grep -q '^ark-backend\.service'; then
    systemctl restart ark-backend.service 2>/dev/null || true
  else
    pkill -f '/root/Knight/.venv/bin/python run.py' 2>/dev/null || true
    sleep 1
    (
      cd backend
      ARK_HOST="${ARK_HOST:-0.0.0.0}" ARK_PORT="$SERVER_HTTP_PORT" nohup setsid ./start.sh > server.log 2>&1 < /dev/null &
    )
  fi
  for _ in 1 2 3 4 5; do
    if curl -sf -m 3 "http://127.0.0.1:${SERVER_HTTP_PORT}/healthz" >/dev/null 2>&1; then
      log "backend restarted"
      return 0
    fi
    sleep 2
  done
  log "ERROR: backend failed to restart"
  return 1
}

ensure_mitm() {
  if pgrep -f 'mitmdump.*reverse:http://127.0.0.1:8443.*listen-port 4443' >/dev/null 2>&1; then
    return 0
  fi
  log "TLS reverse proxy unhealthy, restarting"
  if systemctl list-unit-files ark-mitm.service 2>/dev/null | grep -q '^ark-mitm\.service'; then
    systemctl restart ark-mitm.service 2>/dev/null || true
  else
    pkill -f 'mitmdump.*reverse:http://127.0.0.1:8443' 2>/dev/null || true
    sleep 1
    (
      cd bridge
      nohup setsid /root/Knight/.venv/bin/mitmdump \
        --mode reverse:http://127.0.0.1:8443 \
        --listen-host 127.0.0.1 --listen-port 4443 \
        -w "flows/reverse-$(date +%Y%m%d-%H%M%S).mitm" \
        > reverse.log 2>&1 < /dev/null &
    )
  fi
  for _ in 1 2 3 4 5; do
    if ss -ltn 2>/dev/null | grep -q ':4443 '; then
      log "TLS reverse proxy restarted"
      return 0
    fi
    sleep 2
  done
  log "ERROR: TLS reverse proxy failed to restart"
  return 1
}

check_once() {
  rotate_log
  ensure_disk_space
  ensure_adb || return 1
  ensure_reverse "$SERVER_HTTP_PORT" "$SERVER_HTTP_PORT" || return 1
  ensure_reverse "$DEVICE_SDK_PORT" "$MITM_REVERSE_PORT" || return 1
  ensure_proxy_off
  ensure_hosts
  ensure_https_forward
  ensure_pay_routes
  ensure_backend || return 1
  ensure_mitm || return 1
  return 0
}

if [[ "${1:-}" == "--loop" ]]; then
  exec 9>logs/.watchdog.lock
  if ! flock -n 9; then
    exit 0
  fi
  log "watchdog started (interval=${ARK_WATCHDOG_INTERVAL}s, serial=$ADB_SERIAL)"
  while true; do
    if ! check_once; then
      log "check failed; retrying in ${ARK_WATCHDOG_INTERVAL}s"
    fi
    sleep "$ARK_WATCHDOG_INTERVAL"
  done
else
  check_once
  log "check ok"
fi
