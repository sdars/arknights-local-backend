#!/system/bin/sh

# APatch runs module_installer.sh in boot mode; keep this hook non-interactive
# so `apd module install` can be driven over adb without an output fd.
echo "- Arknights Local Backend Bridge" >&2
echo "- Official APK is not modified" >&2
