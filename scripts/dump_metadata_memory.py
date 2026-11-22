#!/usr/bin/env python3
"""Dump the decrypted global-metadata mapping from a rooted running client."""

import argparse
import re
import struct
import subprocess
from pathlib import Path


MAP_RE = re.compile(r"^([0-9a-f]+)-([0-9a-f]+)\s+(\S+)\s+\S+\s+\S+\s+\S+\s*(.*)$")


def run(args, timeout=30):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--map-start", type=lambda value: int(value, 0), default=0)
    args = parser.parse_args()
    serial = ["adb", "-s", args.serial]

    maps_text = run(serial + ["shell", "su", "-c", f"cat /proc/{args.pid}/maps"]).stdout
    candidates = []
    preferred = []
    for line in maps_text.splitlines():
        match = MAP_RE.match(line)
        if not match:
            continue
        start, end = int(match.group(1), 16), int(match.group(2), 16)
        name = match.group(4).strip()
        size = end - start
        if 20 * 1024 * 1024 <= size <= 100 * 1024 * 1024 and ("/dev/zero" in name or not name):
            row = (start, size, name)
            candidates.append(row)
            if "/dev/zero" in name and 35 * 1024 * 1024 <= size <= 60 * 1024 * 1024:
                preferred.append(row)
    if args.map_start:
        candidates = [row for row in candidates if row[0] == args.map_start]
        preferred = [row for row in preferred if row[0] == args.map_start]
    if not candidates:
        raise SystemExit("no plausible anonymous metadata mapping; pass --map-start")
    pool = preferred or candidates
    start, size, name = max(pool, key=lambda row: row[1])
    print(f"mapping {start:#x}-{start + size:#x} ({size} bytes) {name}")

    remote = "/data/local/tmp/ark-metadata-mem.bin"
    command = (
        f"dd if=/proc/{args.pid}/mem of={remote} bs=4096 "
        f"skip={start // 4096} count={size // 4096} 2>&1; "
        f"stat -c %s {remote}"
    )
    copied = run(serial + ["shell", "su", "-c", command], timeout=120).stdout
    print(copied.strip())
    local_tmp = args.output.with_suffix(args.output.suffix + ".remote")
    subprocess.run(serial + ["pull", remote, str(local_tmp)], check=True)
    raw = local_tmp.read_bytes()
    if len(raw) != size:
        raise SystemExit(f"dump length mismatch: got {len(raw)}, expected {size}")
    # The runtime copy has an all-zero 8-byte prefix. Restore the standard
    # v29 header (the section table at 0x8 is already decrypted).
    decoded = bytearray(raw)
    declared_size = struct.unpack_from("<I", decoded, 0x108)[0]
    if 20 * 1024 * 1024 <= declared_size <= len(decoded):
        decoded = decoded[:declared_size]
    decoded[:8] = struct.pack("<II", 0xFAB11BAF, 29)
    args.output.write_bytes(decoded)
    local_tmp.unlink(missing_ok=True)
    print(f"wrote {args.output} ({len(decoded)} bytes), magic={decoded[:4].hex()}")


if __name__ == "__main__":
    main()
