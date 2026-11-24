import json
import threading
import time
from pathlib import Path

from .config import BASE_DIR
from . import game_data


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


def skin_good_list(now=None):
    """Drop expired and non-shop skin offers.

    The fixture was captured from an older shop rotation and mixes in
    skins that are only obtainable from events (``obtainApproach`` is not
    ``采购中心``).  The client renders those as a red SHOP CLOSED brand
    placeholder.  Keep purchasable, currently-valid offers only; explicit
    null timestamps also crash the client deserializer.
    """
    now = int(now or time.time())
    data = load_shop("SkinGoodList.json") or {}
    skins = (game_data.excel("skin_table.json") or {}).get("charSkins") or {}
    goods = []
    for raw in data.get("goodList") or []:
        if not isinstance(raw, dict):
            continue
        start = raw.get("startDateTime")
        end = raw.get("endDateTime")
        if end is not None and int(end or 0) <= now:
            continue
        if start is not None and int(start or 0) > now:
            continue
        skin = skins.get(str(raw.get("skinId") or "")) or {}
        display = skin.get("displaySkin") or {}
        if "采购中心" not in str(display.get("obtainApproach") or ""):
            continue
        goods.append(dict(raw))
    return {
        "goodList": goods,
        "playerDataDelta": {"modified": {}, "deleted": {}},
    }
