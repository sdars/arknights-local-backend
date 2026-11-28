#!/system/bin/sh
# Late-start service: keep the official client's local route usable without
# touching its APK.  Host-side adb reverse remains the transport in adb mode.

MODDIR=${0%/*}
STATE=/data/adb/ark-local
CONFIG="$STATE/config.conf"
mkdir -p "$STATE" 2>/dev/null || true

DEVICE_HTTPS_PORT=${DEVICE_HTTPS_PORT:-443}
DEVICE_SDK_PORT=${DEVICE_SDK_PORT:-8444}
PACKAGES=${PACKAGES:-"com.hypergryph.arknights com.hypergryph.arknights.bilibili"}
ROUTES=${ROUTES:-"110.75.170.0/24 110.75.231.202/32 110.75.236.202/32 110.75.244.202/32 110.76.18.202/32 43.145.18.0/24 43.174.224.0/23"}

[ -f "$CONFIG" ] && . "$CONFIG"

LOG="$STATE/service.log"
STATUS="$STATE/status"

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" >> "$LOG" 2>/dev/null || true
}

rotate_log() {
  [ -f "$LOG" ] || return 0
  size=$(wc -c < "$LOG" 2>/dev/null | tr -dc '0-9')
  [ -n "$size" ] && [ "$size" -gt 1048576 ] && mv -f "$LOG" "$LOG.1" 2>/dev/null || true
}

relay_running() {
  ps -A -o ARGS 2>/dev/null | grep -q "[n]c -p $1 -L nc 127.0.0.1 $2" >/dev/null 2>&1
}

ensure_relay() {
  if relay_running "$DEVICE_HTTPS_PORT" "$DEVICE_SDK_PORT"; then
    return 0
  fi
  nohup nc -p "$DEVICE_HTTPS_PORT" -L nc 127.0.0.1 "$DEVICE_SDK_PORT" </dev/null >/dev/null 2>&1 &
  log "started ${DEVICE_HTTPS_PORT} -> 127.0.0.1:${DEVICE_SDK_PORT} relay"
}

uid_for_package() {
  dumpsys package "$1" 2>/dev/null | grep -m1 'userId=' | sed 's/.*userId=//' | tr -dc '0-9'
}

ensure_routes() {
  iptables -t nat -N ARK_LOCAL 2>/dev/null || true
  iptables -t nat -F ARK_LOCAL 2>/dev/null || true
  for route in $ROUTES; do
    iptables -t nat -A ARK_LOCAL -d "$route" -p tcp --dport "$DEVICE_HTTPS_PORT" -j DNAT --to-destination "127.0.0.1:${DEVICE_SDK_PORT}" 2>/dev/null || true
  done
  found=""
  for pkg in $PACKAGES; do
    uid=$(uid_for_package "$pkg")
    [ -n "$uid" ] || continue
    found="$found $uid"
    iptables -t nat -C OUTPUT -m owner --uid-owner "$uid" -p tcp --dport "$DEVICE_HTTPS_PORT" -j ARK_LOCAL 2>/dev/null || \
      iptables -t nat -I OUTPUT 1 -m owner --uid-owner "$uid" -p tcp --dport "$DEVICE_HTTPS_PORT" -j ARK_LOCAL 2>/dev/null || true
  done
  UIDS="$found"
}

ensure_proxy_off() {
  current=$(settings get global http_proxy 2>/dev/null | tr -d '\r')
  if [ "$current" != ":0" ] && [ "$current" != "null" ] && [ -n "$current" ]; then
    settings put global http_proxy :0 >/dev/null 2>&1 || true
  fi
}

write_status() {
  relay=no
  relay_running "$DEVICE_HTTPS_PORT" "$DEVICE_SDK_PORT" && relay=yes
  {
    printf 'updated=%s\n' "$(date '+%F %T')"
    printf 'module=ark-local\n'
    printf 'mode=adb\n'
    printf 'relay_%s=%s\n' "$DEVICE_HTTPS_PORT" "$relay"
    printf 'uids=%s\n' "${UIDS:-}"
    printf 'routes=%s\n' "$(printf '%s\n' "$ROUTES" | wc -w | tr -d ' ')"
    printf 'http_proxy=%s\n' "$(settings get global http_proxy 2>/dev/null | tr -d '\r')"
  } > "$STATUS" 2>/dev/null || true
}

watchdog() {
  while true; do
    rotate_log
    ensure_proxy_off
    ensure_relay
    ensure_routes
    write_status
    sleep 15
  done
}

# Avoid duplicate watchdog loops when the service entry is triggered twice.
if [ -f "$STATE/watchdog.pid" ]; then
  old=$(cat "$STATE/watchdog.pid" 2>/dev/null)
  [ -n "$old" ] && kill "$old" 2>/dev/null || true
fi

# Wait for boot completion when called from the manager; a manual run after
# boot returns immediately.
i=0
while [ "$(getprop sys.boot_completed 2>/dev/null)" != "1" ] && [ "$i" -lt 120 ]; do
  sleep 2
  i=$((i + 1))
done

ensure_proxy_off
ensure_relay
ensure_routes
write_status
watchdog </dev/null >/dev/null 2>&1 &
echo $! > "$STATE/watchdog.pid" 2>/dev/null || true
log "service started (pid=$!)"
