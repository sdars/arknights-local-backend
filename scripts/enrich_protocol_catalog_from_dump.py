#!/usr/bin/env python3
"""Attach field offsets and method RVAs from dump.cs to the protocol catalog."""

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMAGE_RE = re.compile(r"^// Image (\d+): (.+?) - (\d+)$")
TYPE_RE = re.compile(r"\b(class|struct|interface|enum)\s+([^\s:]+)")
FIELD_RE = re.compile(r"([^\s;]+)\s*;\s*//\s*0x([0-9A-Fa-f]+)")
RVA_RE = re.compile(r"//\s*RVA:\s*0x([0-9A-Fa-f]+).*?(?:Slot:\s*(\d+))?")
METHOD_RE = re.compile(r"([^\s(]+)\s*\(")


def newest(pattern):
    paths = [path for path in ROOT.glob(pattern) if path.is_file() and "tools" not in path.parts]
    return max(paths, key=lambda path: path.stat().st_mtime) if paths else None


def full_name(namespace, type_name):
    return f"{namespace}.{type_name}" if namespace else type_name


def image_for_type(images, type_index):
    current = ""
    for image in images:
        if image["type_start"] <= type_index:
            current = image["name"]
        else:
            break
    return current


def parse_block(block):
    fields = []
    methods = []
    pending_rva = None
    for line in block:
        stripped = line.strip()
        if stripped.startswith("// RVA:"):
            match = RVA_RE.search(stripped)
            if match:
                pending_rva = {
                    "rva": int(match.group(1), 16),
                    "slot": int(match.group(2)) if match.group(2) is not None else None,
                }
            continue
        if pending_rva is not None and stripped and not stripped.startswith("//"):
            match = METHOD_RE.search(stripped)
            if match:
                pending_rva.update(
                    {
                        "name": match.group(1),
                        "signature": stripped.rstrip(" {").rstrip(),
                    }
                )
                methods.append(pending_rva)
            pending_rva = None
            continue
        if "; // 0x" in stripped:
            match = FIELD_RE.search(stripped)
            if match:
                fields.append({"name": match.group(1), "offset": int(match.group(2), 16)})
    return fields, methods


def enrich(catalog, dump_path):
    targets = {item["name"]: item for item in catalog.get("protocol_types", []) if "fields" in item}
    images = []
    namespace = ""
    enriched = 0
    with dump_path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_no, line in enumerate(stream, 1):
            match = IMAGE_RE.match(line.rstrip("\n"))
            if match:
                images.append(
                    {
                        "index": int(match.group(1)),
                        "name": match.group(2),
                        "type_start": int(match.group(3)),
                    }
                )
                continue
            if line.startswith("// Namespace:"):
                namespace = line.split(":", 1)[1].strip()
                continue
            if "// TypeDefIndex:" not in line:
                continue
            decl = TYPE_RE.search(line)
            if not decl:
                continue
            type_name = decl.group(2)
            name = full_name(namespace, type_name)
            item = targets.get(name)
            if item is None:
                continue
            type_index = int(line.split("// TypeDefIndex:", 1)[1].strip())
            block = [line]
            depth = 0
            started = False
            for block_line in stream:
                block.append(block_line)
                depth += block_line.count("{") - block_line.count("}")
                if "{" in block_line:
                    started = True
                if started and depth <= 0:
                    break
            fields, methods = parse_block(block)
            item["dump"] = {
                "image": image_for_type(images, type_index),
                "type_def_index": type_index,
                "line": line_no,
                "fields": fields,
                "methods": methods,
            }
            field_pool = defaultdict(list)
            for field in item.get("fields", []):
                field_pool[field["name"]].append(field)
            for field in fields:
                if field_pool[field["name"]]:
                    field_pool[field["name"]].pop(0).update(field)
            method_pool = defaultdict(list)
            for method in item.get("methods", []):
                method_pool[method["name"]].append(method)
            for method in methods:
                if method_pool[method["name"]]:
                    target = method_pool[method["name"]].pop(0)
                    target.update({key: method[key] for key in ("rva", "slot", "signature")})
            enriched += 1
    catalog.setdefault("artifacts", {})
    catalog["artifacts"].update(
        {
            "dump_cs": str(dump_path.relative_to(ROOT)) if dump_path.is_relative_to(ROOT) else str(dump_path),
            "dump_cs_size": dump_path.stat().st_size,
            "dump_cs_sha256": hashlib.sha256(dump_path.read_bytes()).hexdigest(),
        }
    )
    catalog.setdefault("counts", {})["dump_enriched_types"] = enriched
    return enriched


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=ROOT / "backend" / "data" / "protocol_catalog.json")
    parser.add_argument("--dump", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    dump_path = args.dump or newest("tmp/逆向分析/**/dump.cs")
    if not dump_path:
        raise SystemExit("dump.cs not found")
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    enriched = enrich(catalog, dump_path)
    output = args.output or args.catalog
    output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"enriched_types": enriched, "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
