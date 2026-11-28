#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ROOT=$(pwd)
SRC="$ROOT/deploy/magisk/ark-local"
BUILD="${1:-$ROOT/tmp/ark-local-module}"
ZIP="${2:-$ROOT/tmp/ark-local-module.zip}"
CA="${MITM_CA_CERT:-/root/.mitmproxy/mitmproxy-ca-cert.pem}"

if [ ! -f "$CA" ]; then
  echo "missing mitmproxy CA: $CA" >&2
  echo "set MITM_CA_CERT=/path/to/mitmproxy-ca-cert.pem" >&2
  exit 1
fi
if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required to compute the Android CA hash" >&2
  exit 1
fi
if ! command -v zip >/dev/null 2>&1; then
  echo "zip is required" >&2
  exit 1
fi

HASH=$(openssl x509 -in "$CA" -noout -subject_hash_old | head -1 | tr -d '\r')
if [ -z "$HASH" ]; then
  echo "could not compute CA subject_hash_old" >&2
  exit 1
fi

rm -rf "$BUILD" "$ZIP"
mkdir -p "$BUILD"
cp -a "$SRC/." "$BUILD/"
mkdir -p "$BUILD/system/etc/security/cacerts"
cp "$CA" "$BUILD/system/etc/security/cacerts/${HASH}.0"
chmod 644 "$BUILD/system/etc/hosts" "$BUILD/system/etc/security/cacerts/${HASH}.0"
find "$BUILD" -name '*.sh' -exec chmod 755 {} \;
find "$BUILD" -type f ! -name '*.sh' -exec chmod 644 {} \;
find "$BUILD" -type d -exec chmod 755 {} \;

(cd "$BUILD" && zip -qr "$ZIP" .)

echo "built: $ZIP"
echo "module: $(grep '^id=' "$BUILD/module.prop" | cut -d= -f2)"
echo "ca:     ${HASH}.0 ($(sha256sum "$CA" | awk '{print $1}'))"
echo "files:"
unzip -l "$ZIP" | sed -n '1,40p'
