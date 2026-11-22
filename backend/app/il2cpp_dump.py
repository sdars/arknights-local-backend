"""Read the full Il2CppDumper output without loading 80 MB into memory."""

import hashlib
import json
import re
import threading
from pathlib import Path

from .config import BASE_DIR


ROOT = BASE_DIR.parent
_lock = threading.Lock()
_cache = {"mtime": None, "path": None, "summary": None}

IMAGE_RE = re.compile(r"^// Image (\d+): (.+?) - (\d+)$")
TYPE_RE = re.compile(r"\b(class|struct|interface|enum)\s+([^\s:]+)")


def _newest(pattern):
    paths = [
        path
        for path in ROOT.glob(pattern)
        if path.is_file() and "il2cppdumper" not in path.parts and "tools" not in path.parts
    ]
    return max(paths, key=lambda path: path.stat().st_mtime) if paths else None


def dump_path():
    return _newest("tmp/逆向分析/**/dump.cs")


def _count_occurrences(path, needle):
    if not path or not path.exists():
        return 0
    total = 0
    tail = b""
    needle_bytes = needle.encode("utf-8")
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(4 * 1024 * 1024)
            if not chunk:
                break
            data = tail + chunk
            total += data.count(needle_bytes)
            tail = data[-(len(needle_bytes) - 1) :] if len(needle_bytes) > 1 else b""
    return total


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def summary():
    path = dump_path()
    if not path:
        return {"exists": False, "detail": "未找到 dump.cs"}
    mtime = path.stat().st_mtime
    with _lock:
        if _cache["summary"] is not None and _cache["mtime"] == mtime and _cache["path"] == path:
            return _cache["summary"]

    images = []
    type_count = 0
    method_count = 0
    line_count = 0
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            line_count += 1
            match = IMAGE_RE.match(line.rstrip("\n"))
            if match:
                images.append(
                    {
                        "index": int(match.group(1)),
                        "name": match.group(2),
                        "type_start": int(match.group(3)),
                    }
                )
            elif "// TypeDefIndex:" in line:
                type_count += 1
            elif "// RVA:" in line:
                method_count += 1

    sibling = path.parent
    stringliteral_path = sibling / "stringliteral.json"
    script_path = sibling / "script.json"
    string_count = 0
    if stringliteral_path.exists():
        try:
            string_count = len(json.loads(stringliteral_path.read_text(encoding="utf-8")))
        except Exception:
            string_count = 0
    script_methods = _count_occurrences(script_path, '"Address":')
    data = {
        "exists": True,
        "path": str(path.relative_to(ROOT)),
        "size": path.stat().st_size,
        "size_human": f"{path.stat().st_size / 1024 / 1024:.1f} MB",
        "mtime": path.stat().st_mtime,
        "sha256": _hash_file(path),
        "lines": line_count,
        "images": images,
        "image_count": len(images),
        "type_count": type_count,
        "method_count": method_count,
        "stringliteral": {
            "exists": stringliteral_path.exists(),
            "path": str(stringliteral_path.relative_to(ROOT)) if stringliteral_path.exists() else "",
            "count": string_count,
            "size": stringliteral_path.stat().st_size if stringliteral_path.exists() else 0,
        },
        "script": {
            "exists": script_path.exists(),
            "path": str(script_path.relative_to(ROOT)) if script_path.exists() else "",
            "methods": script_methods,
            "size": script_path.stat().st_size if script_path.exists() else 0,
        },
    }
    with _lock:
        _cache.update(mtime=mtime, path=path, summary=data)
    return data


def _image_for_type(images, type_index):
    current = None
    for image in images:
        if image["type_start"] <= type_index:
            current = image["name"]
        else:
            break
    return current or ""


def _full_name(namespace, type_name):
    return f"{namespace}.{type_name}" if namespace else type_name


def search(query, limit=80):
    path = dump_path()
    if not path:
        return [], 0
    needle = query.strip().lower()
    if not needle:
        return [], 0
    rows = []
    total = 0
    namespace = ""
    current_type = ""
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_no, line in enumerate(stream, 1):
            if line.startswith("// Namespace:"):
                namespace = line.split(":", 1)[1].strip()
                continue
            if "// TypeDefIndex:" in line:
                match = TYPE_RE.search(line)
                current_type = _full_name(namespace, match.group(2)) if match else namespace
            if needle not in line.lower():
                continue
            total += 1
            if len(rows) >= limit:
                continue
            text = line.strip()
            if "// TypeDefIndex:" in line:
                kind = "type"
            elif "// RVA:" in text and not text.startswith("//"):
                kind = "method"
            elif "; // 0x" in text:
                kind = "field"
            elif "(" in text and text.endswith(")"):
                kind = "method"
            else:
                kind = "line"
            rows.append(
                {
                    "line": line_no,
                    "kind": kind,
                    "namespace": namespace,
                    "type": current_type,
                    "text": text,
                }
            )
    return rows, total


def get_type(name):
    path = dump_path()
    target = name.strip()
    if not path or not target:
        return None
    info = summary()
    namespace = ""
    pending = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_no, line in enumerate(stream, 1):
            if line.startswith("// Namespace:"):
                namespace = line.split(":", 1)[1].strip()
                pending = [line]
                continue
            pending.append(line)
            if len(pending) > 80:
                pending.pop(0)
            if "// TypeDefIndex:" not in line:
                continue
            match = TYPE_RE.search(line)
            if not match:
                continue
            type_name = match.group(2)
            full_name = _full_name(namespace, type_name)
            if target not in (full_name, type_name) and not full_name.endswith("." + target):
                continue
            type_index = int(line.split("// TypeDefIndex:", 1)[1].strip())
            block = list(pending)
            depth = 0
            started = False
            truncated = False
            for block_line in stream:
                block.append(block_line)
                depth += block_line.count("{") - block_line.count("}")
                if "{" in block_line:
                    started = True
                if started and depth <= 0:
                    break
                if len(block) >= 5000:
                    truncated = True
                    break
            return {
                "name": full_name,
                "short_name": type_name,
                "namespace": namespace,
                "type_def_index": type_index,
                "image": _image_for_type(info.get("images", []), type_index),
                "line": line_no,
                "text": "".join(block),
                "truncated": truncated,
            }
    return None
