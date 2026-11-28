#!/system/bin/sh
# Early boot hook: make the module's hosts/CA survive managers that only
# merge directories, and give netd a readable system_file context.

MODDIR=${0%/*}
STATE=/data/adb/ark-local

mkdir -p "$STATE" 2>/dev/null || true
chmod 700 "$STATE" 2>/dev/null || true

HOSTS="$MODDIR/system/etc/hosts"
if [ -f "$HOSTS" ]; then
  # Remove a previous bind mount before remounting; repeated module installs
  # otherwise stack mounts and leave netd with stale contexts.
  umount /system/etc/hosts 2>/dev/null || true
  chmod 644 "$HOSTS" 2>/dev/null || true
  chcon u:object_r:system_file:s0 "$HOSTS" 2>/dev/null || true
  mount --bind "$HOSTS" /system/etc/hosts 2>/dev/null || true
fi

CA_SRC="$MODDIR/system/etc/security/cacerts"
CA_STATE="$STATE/cacerts"
if [ -d "$CA_SRC" ]; then
  umount /system/etc/security/cacerts 2>/dev/null || true
  mkdir -p "$CA_STATE" 2>/dev/null || true
  rm -rf "$CA_STATE"/* 2>/dev/null || true
  # Keep the platform trust store; append the local mitm CA.  The copy is
  # mounted over the system directory so the CA works even when the manager
  # does not merge nested system/etc/security directories.
  cp -a /system/etc/security/cacerts/. "$CA_STATE/" 2>/dev/null || true
  cp -a "$CA_SRC/"*.0 "$CA_STATE/" 2>/dev/null || true
  chown -R 0:0 "$CA_STATE" 2>/dev/null || true
  chmod 755 "$CA_STATE" 2>/dev/null || true
  chmod 644 "$CA_STATE"/* 2>/dev/null || true
  chcon -R u:object_r:system_file:s0 "$CA_STATE" 2>/dev/null || true
  ca_count=$(ls "$CA_STATE" 2>/dev/null | wc -l | tr -dc '0-9')
  # Never hide the platform store behind an incomplete copy.
  if [ -n "$ca_count" ] && [ "$ca_count" -ge 50 ]; then
    mount --bind "$CA_STATE" /system/etc/security/cacerts 2>/dev/null || true
  fi
fi

printf 'module_active=1\npost_fs_data=%s\n' "$(date '+%F %T')" > "$STATE/module.status" 2>/dev/null || true
