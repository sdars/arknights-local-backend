#!/usr/bin/env python3
"""Extract a reviewable route/type index from decrypted IL2CPP metadata.

This does not pretend to be a decompiler. It reads the v29 metadata tables that
are present after the client has decrypted them and extracts the exact route
strings plus protocol-related type, field and method names. The output is the
static source used to drive tests/protocol.
"""

import argparse
import hashlib
import json
import re
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

ROUTE_PREFIXES = (
    "account/", "quest/", "user/", "u8/", "general/", "app/", "config/",
    "charBuild/", "story/", "pay/", "social/", "building/", "gacha/",
    "shop/", "mission/", "mail/", "background/", "announcement/",
    "activity/", "retro/", "campaign", "crisis", "rlv2/", "roguelike/",
    "sandboxPerm/", "tower/", "deepSea/", "rune/", "charm/", "invite/",
    "templateShop/", "templateTrap/", "reslock/", "depot/", "medal/",
    "businessCard/", "charRotation/", "char/", "trainingGround/", "vecBreak",
    "tower/", "siracusaMap/", "arkodc/", "gallery/", "firework/",
    "mainline/", "mainlineClue/", "npcAudio/", "setting/", "storyreview/",
    "autoChessPerm/", "captcha/", "online/", "admin/",
)

PROTOCOL_NAME = re.compile(
    r"(Request|Response|Protocol|Network|API|Api|Server|Http|Socket|SDK|Gacha|Battle|Sync|Login)",
    re.IGNORECASE,
)


def u32(blob, offset):
    return struct.unpack_from("<I", blob, offset)[0]


def i32(blob, offset):
    return struct.unpack_from("<i", blob, offset)[0]


def read_header(blob):
    if blob[:4] != b"\xaf\x1b\xb1\xfa":
        raise SystemExit("metadata is not a standard MAGIC file; decrypt it first")
    version = i32(blob, 4)
    if version != 29:
        raise SystemExit(f"only metadata v29 is supported by this indexer, got {version}")
    pos = 8
    values = {}
    for name in HEADER_FIELDS:
        values[name] = {"offset": u32(blob, pos), "size": i32(blob, pos + 4)}
        pos += 8
    return version, values


def read_string(blob, header, index):
    base = header["string"]["offset"]
    end = base + header["string"]["size"]
    pos = base + index
    if pos < base or pos >= end:
        return None
    stop = blob.find(b"\0", pos, end)
    if stop < 0:
        stop = end
    try:
        return blob[pos:stop].decode("utf-8", "replace")
    except Exception:
        return None


def read_string_literals(blob, header):
    table = header["stringLiteral"]
    data = header["stringLiteralData"]
    count = table["size"] // 8
    for index in range(count):
        offset = table["offset"] + index * 8
        length = u32(blob, offset)
        data_index = u32(blob, offset + 4)
        if length > 512 or data_index + length > data["size"]:
            continue
        raw = blob[data["offset"] + data_index : data["offset"] + data_index + length]
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        yield index, data_index, value


def route_kind(path):
    if path.startswith(("/u8/", "/app/", "/user/oauth2", "/user/info", "/general/")):
        return "sdk"
    if path.startswith(("/account/", "/user/", "/quest/", "/charBuild/", "/story/", "/gacha/", "/shop/")):
        return "core"
    return "feature"


def parse_routes(blob, header):
    routes = {}
    for index, data_index, value in read_string_literals(blob, header):
        if len(value) < 4 or len(value) > 160:
            continue
        if not value.startswith("/") or "//" in value or " " in value or "\\" in value:
            continue
        tail = value[1:]
        if not tail.startswith(ROUTE_PREFIXES):
            continue
        if "<" in value or ">" in value or "=" in value or "?" in value:
            continue
        if value.endswith((".html", ".h", ".proto", ".json", ".dll")):
            continue
        routes.setdefault(value, {"path": value, "kind": route_kind(value), "string_literal_index": index, "data_index": data_index})
    return sorted(routes.values(), key=lambda row: row["path"])


def parse_types(blob, header):
    type_table = header["typeDefinitions"]
    field_table = header["fields"]
    method_table = header["methods"]
    parameter_table = header["parameters"]
    type_size = 92
    field_size = 12
    method_size = 32
    parameter_size = 12
    type_count = type_table["size"] // type_size
    fields = []
    types = []
    for index in range(type_count):
        offset = type_table["offset"] + index * type_size
        name = read_string(blob, header, u32(blob, offset)) or ""
        namespace = read_string(blob, header, u32(blob, offset + 4)) or ""
        field_start = i32(blob, offset + 32)
        method_start = i32(blob, offset + 36)
        # Hypergryph v29 adds one int32 (hgExtra) after interfaceOffsetsStart,
        # before the eight uint16 counts.
        method_count = struct.unpack_from("<H", blob, offset + 68)[0]
        field_count = struct.unpack_from("<H", blob, offset + 72)[0]
        full_name = f"{namespace}.{name}" if namespace else name
        detailed = bool(re.search(r"(Request|Response)$", full_name, re.IGNORECASE))
        field_names = []
        for j in range(max(0, min(field_count, 512)) if detailed else 0):
            pos = field_table["offset"] + (field_start + j) * field_size
            if pos + field_size > len(blob):
                break
            field_name = read_string(blob, header, u32(blob, pos)) or ""
            type_index = i32(blob, pos + 4)
            field_names.append({"name": field_name, "type_index": type_index})
        method_names = []
        for j in range(max(0, min(method_count, 512)) if detailed else 0):
            pos = method_table["offset"] + (method_start + j) * method_size
            if pos + method_size > len(blob):
                break
            method_name = read_string(blob, header, u32(blob, pos)) or ""
            parameter_start = i32(blob, pos + 12)
            parameter_count = struct.unpack_from("<H", blob, pos + 30)[0]
            params = []
            for k in range(max(0, min(parameter_count, 128))):
                pp = parameter_table["offset"] + (parameter_start + k) * parameter_size
                if pp + parameter_size > len(blob):
                    break
                params.append({
                    "name": read_string(blob, header, u32(blob, pp)) or "",
                    "type_index": i32(blob, pp + 8),
                })
            method_names.append({"name": method_name, "parameters": params})
        row = {
            "name": full_name,
            "field_count": field_count,
            "method_count": method_count,
        }
        if detailed:
            row["fields"] = field_names
            row["methods"] = method_names
        types.append(row)
        if PROTOCOL_NAME.search(full_name):
            fields.append(row)
    return types, fields


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lib", type=Path)
    parser.add_argument("--include-all-types", action="store_true")
    args = parser.parse_args()
    blob = args.metadata.read_bytes()
    version, header = read_header(blob)
    routes = parse_routes(blob, header)
    all_types, protocol_types = parse_types(blob, header)
    try:
        source_label = str(args.metadata.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        source_label = str(args.metadata)
    payload = {
        "schema": 1,
        "source": source_label,
        "metadata_version": version,
        "artifacts": {
            "metadata_size": len(blob),
            "metadata_sha256": hashlib.sha256(blob).hexdigest(),
        },
        "header": header,
        "counts": {
            "routes": len(routes),
            "types": len(all_types),
            "protocol_types": len(protocol_types),
            "string_literals": header["stringLiteral"]["size"] // 8,
        },
        "routes": routes,
        "protocol_types": protocol_types,
    }
    if args.lib and args.lib.exists():
        payload["artifacts"].update({
            "libil2cpp_size": args.lib.stat().st_size,
            "libil2cpp_sha256": hashlib.sha256(args.lib.read_bytes()).hexdigest(),
        })
    if args.include_all_types:
        payload["types"] = all_types
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["counts"], ensure_ascii=False))
    print(f"wrote {args.output} ({args.output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
