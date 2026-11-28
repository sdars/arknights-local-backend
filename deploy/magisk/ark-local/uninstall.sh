#!/system/bin/sh

STATE=/data/adb/ark-local

while umount -l /system/etc/hosts 2>/dev/null; do :; done
while umount -l /system/etc/security/cacerts 2>/dev/null; do :; done

if [ -f "$STATE/watchdog.pid" ]; then
  old=$(cat "$STATE/watchdog.pid" 2>/dev/null)
  [ -n "$old" ] && kill "$old" 2>/dev/null || true
fi

# Remove only our app-scoped jump rules, then the dedicated chain.
for uid in $(iptables -t nat -S OUTPUT 2>/dev/null | sed -n 's/.*--uid-owner \([0-9]*\).*ARK_LOCAL.*/\1/p'); do
  while iptables -t nat -D OUTPUT -m owner --uid-owner "$uid" -p tcp --dport 443 -j ARK_LOCAL 2>/dev/null; do :; done
done
iptables -t nat -F ARK_LOCAL 2>/dev/null || true
iptables -t nat -X ARK_LOCAL 2>/dev/null || true

# The relay is owned by this module; the host watchdog may recreate its own
# copy after the next adb reconnect if the module is removed.
for pid in $(ps -A -o PID,ARGS 2>/dev/null | grep -F '[n]c -p 443 -L nc 127.0.0.1 8444' | awk '{print $1}'); do
  kill "$pid" 2>/dev/null || true
done

rm -rf "$STATE" 2>/dev/null || true
