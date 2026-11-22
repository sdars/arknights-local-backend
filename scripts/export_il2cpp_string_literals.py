#!/usr/bin/env python3
"""Export every valid IL2CPP v29 string literal as a dumper-compatible JSON file.

The stock Il2CppDumper 6.7.46 does not populate v29 metadata-usage string
literals, so its stringliteral.json can contain raw metadata words.  This
exporter reads the literal table directly and keeps the data-table address
for each value so route/type analysis has a stable reference.
"""

import argparse
import json
import struct
from pathlib import Path


HEADER_FIELDS = [
    "stringLiteral",
    "stringLiteralData",
    "string",
    "events",
    "properties",
    "methods",
    "parameterDefaultValues",
    "fieldDefaultValues",
    "fieldAndParameterDefaultValueData",
    "fieldMarshaledSizes",
    "parameters",
    "fields",
    "genericParameters",
    "genericParameterConstraints",
    "genericContainers",
    "nestedTypes",
    "interfaces",
    "vtableMethods",
    "interfaceOffsets",
    "typeDefinitions",
    "images",
    "assemblies",
    "fieldRefs",
    "referencedAssemblies",
    "attributeData",
    "attributeDataRange",
    "unresolvedVirtualCallParameterTypes",
    "unresolvedVirtualCallParameterRanges",
    "windowsRuntimeTypeNames",
    "windowsRuntimeStrings",
    "exportedTypeDefinitions",
]


def read_header(blob):
    if blob[:4] != b"\xaf\x1b\xb1\xfa":
        raise SystemExit("metadata is not a standard MAGIC file; decrypt it first")
    version = struct.unpack_from("<i", blob, 4)[0]
    if version != 29:
        raise SystemExit(f"only metadata v29 is supported, got {version}")
    header = {}
    pos = 8
    for name in HEADER_FIELDS:
        header[name] = {
            "offset": struct.unpack_from("<I", blob, pos)[0],
            "size": struct.unpack_from("<i", blob, pos + 4)[0],
        }
        pos += 8
    return version, header


def export_literals(blob, header):
    table = header["stringLiteral"]
    data = header["stringLiteralData"]
    rows = []
    for index in range(table["size"] // 8):
        offset = table["offset"] + index * 8
        length, data_index = struct.unpack_from("<II", blob, offset)
        if length > 4096 or data_index + length > data["size"]:
            continue
        start = data["offset"] + data_index
        raw = blob[start : start + length]
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        rows.append(
            {
                "value": value,
                "address": f"0x{start:X}",
                "index": index,
                "data_index": data_index,
                "length": length,
            }
        )
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    blob = args.metadata.read_bytes()
    _, header = read_header(blob)
    rows = export_literals(blob, header)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"string_literals": len(rows), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
