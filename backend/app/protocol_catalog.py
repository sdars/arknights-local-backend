import json
import subprocess
import sys
import threading
from pathlib import Path

from .config import BASE_DIR


ROOT = BASE_DIR.parent
CATALOG_PATH = BASE_DIR / "data" / "protocol_catalog.json"
REBUILD_LOG = ROOT / "logs" / "catalog-rebuild.log"

_lock = threading.Lock()
_cache = {"mtime": None, "data": None}


def load():
    if not CATALOG_PATH.exists():
        return {"counts": {}, "routes": [], "protocol_types": [], "artifacts": {}}
    mtime = CATALOG_PATH.stat().st_mtime
    with _lock:
        if _cache["data"] is not None and _cache["mtime"] == mtime:
            return _cache["data"]
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        _cache.update(mtime=mtime, data=data)
        return data


def invalidate():
    with _lock:
        _cache.update(mtime=None, data=None)


def summary():
    data = load()
    routes = data.get("routes", [])
    groups = {}
    for route in routes:
        path = route.get("path", "")
        group = path.strip("/").split("/", 1)[0] or "root"
        groups[group] = groups.get(group, 0) + 1
    return {
        "counts": data.get("counts", {}),
        "detailed_types": sum(1 for item in data.get("protocol_types", []) if "fields" in item),
        "artifacts": data.get("artifacts", {}),
        "groups": sorted(groups.items(), key=lambda item: (-item[1], item[0])),
        "catalog_mtime": CATALOG_PATH.stat().st_mtime if CATALOG_PATH.exists() else None,
    }


def search_routes(query="", kind="", limit=200):
    data = load()
    needle = query.strip().lower()
    rows = []
    for route in data.get("routes", []):
        path = route.get("path", "")
        if needle and needle not in path.lower():
            continue
        if kind and route.get("kind") != kind:
            continue
        rows.append(route)
    rows.sort(key=lambda row: row.get("path", ""))
    return rows[: max(1, min(int(limit), 500))], len(rows)


def search_types(query="", limit=100, detailed_only=False):
    data = load()
    needle = query.strip().lower()
    rows = []
    for item in data.get("protocol_types", []):
        name = item.get("name", "")
        if needle and needle not in name.lower():
            continue
        if detailed_only and "fields" not in item:
            continue
        rows.append(item)
    rows.sort(key=lambda item: item.get("name", ""))
    return rows[: max(1, min(int(limit), 300))], len(rows)


def get_type(name):
    for item in load().get("protocol_types", []):
        if item.get("name") == name:
            return item
    return None


def _newest(pattern):
    paths = [p for p in ROOT.glob(pattern) if p.is_file() and "il2cppdumper" not in p.parts and "tools" not in p.parts]
    return max(paths, key=lambda p: p.stat().st_mtime) if paths else None


def rebuild_from_memory_dump():
    metadata = _newest("tmp/逆向分析/**/global-metadata.decrypted.dat")
    lib = _newest("tmp/逆向分析/**/libil2cpp.so")
    dump = _newest("tmp/逆向分析/**/dump.cs")
    if not metadata:
        return {"ok": False, "output": "未找到 global-metadata.decrypted.dat", "catalog": summary()}
    script = ROOT / "scripts" / "extract_protocol_catalog.py"
    cmd = [sys.executable, str(script), str(metadata), "--output", str(CATALOG_PATH)]
    if lib:
        cmd.extend(["--lib", str(lib)])
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=180, check=False)
        output = (proc.stdout + proc.stderr).strip()
        rc = proc.returncode
        if rc == 0 and dump:
            enrich = ROOT / "scripts" / "enrich_protocol_catalog_from_dump.py"
            proc = subprocess.run(
                [sys.executable, str(enrich), "--catalog", str(CATALOG_PATH), "--dump", str(dump)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            output += "\n" + (proc.stdout + proc.stderr).strip()
            rc = proc.returncode
    except Exception as exc:
        rc = 124
        output = str(exc)
    REBUILD_LOG.parent.mkdir(parents=True, exist_ok=True)
    REBUILD_LOG.write_text(f"rc={rc}\nmetadata={metadata}\nlib={lib}\ndump={dump}\n{output}\n", encoding="utf-8")
    invalidate()
    return {"ok": rc == 0, "rc": rc, "output": output, "catalog": summary()}
