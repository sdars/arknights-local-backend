import json
import threading
from pathlib import Path

from .config import BASE_DIR


SHOP_DIR = BASE_DIR / "fixtures" / "shop"
_lock = threading.Lock()
_cache = {}


def load_shop(filename):
    path = SHOP_DIR / filename
    mtime = path.stat().st_mtime if path.exists() else None
    with _lock:
        cached = _cache.get(filename)
        if cached and cached[0] == mtime:
            return cached[1]
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        _cache[filename] = (mtime, data)
        return data


def shop_response(filename):
    data = load_shop(filename)
    delta = {"modified": {}, "deleted": {}}
    if data is None:
        return {"result": 0, "playerDataDelta": delta}
    if isinstance(data, list):
        return {"goodList": data, "playerDataDelta": delta}
    result = {key: value for key, value in data.items() if key != "playerDataDelta"}
    result["playerDataDelta"] = delta
    return result
