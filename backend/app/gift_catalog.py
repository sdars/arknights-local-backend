import json
import threading
from pathlib import Path

from .config import BASE_DIR
from . import game_data


CATALOG_PATH = BASE_DIR / "data" / "gift_packages.json"
GP_GOODS_PATH = BASE_DIR / "fixtures" / "shop" / "GPGoodList.json"
_lock = threading.Lock()
_catalog = None
_current = None


def _load(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _walk_gp(payload):
    rows = {}
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            good_id = node.get("goodId")
            if good_id and isinstance(node.get("items"), list):
                rows[str(good_id)] = node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return rows


def _ensure():
    global _catalog, _current
    if _catalog is not None and _current is not None:
        return
    with _lock:
        if _catalog is None:
            raw = _load(CATALOG_PATH, {})
            _catalog = raw if isinstance(raw, dict) else {}
        if _current is None:
            _current = _walk_gp(_load(GP_GOODS_PATH, {}))


def catalog():
    _ensure()
    return _catalog


def current_goods():
    _ensure()
    return _current


def entry(good_id):
    good_id = str(good_id or "")
    _ensure()
    current = _current.get(good_id)
    if current:
        return {
            **current,
            "goodId": good_id,
            "displayName": current.get("displayName") or current.get("name") or good_id,
            "items": list(current.get("items") or []),
            "source": "gp-good-list",
        }
    row = _catalog.get(good_id)
    if row:
        return dict(row)
    return {}


def items(good_id):
    return [dict(item) for item in entry(good_id).get("items") or []]


def display_name(good_id):
    row = entry(good_id)
    return str(row.get("displayName") or row.get("name") or good_id or "")


def _shop_groups():
    return {
        "levelGP": "level",
        "oneTimeGP": "oneTime",
        "monthlySub": "paymentCenter",
    }


def section(good_id):
    good_id = str(good_id or "")
    _ensure()
    payload = _load(GP_GOODS_PATH, {})
    monthly = (payload.get("monthlyGroup") or {}).get("packages") or {}
    if good_id in monthly:
        return "monthly"
    weekly = (payload.get("weeklyGroup") or {}).get("packages") or {}
    if good_id in weekly:
        return "weekly"
    for key, target in _shop_groups().items():
        for row in payload.get(key) or []:
            if isinstance(row, dict) and str(row.get("goodId") or "") == good_id:
                return target
    if good_id == "GP_mCard_1":
        return "paymentCenter"
    return "oneTime"


def _info_list(user, target):
    shop = user.setdefault("shop", {})
    gp = shop.setdefault("GP", {})
    node = gp.setdefault(target, {})
    info = node.setdefault("info", [])
    if not isinstance(info, list):
        info = []
        node["info"] = info
    return info


def purchase_count(user, good_id):
    good_id = str(good_id or "")
    for target in ("oneTime", "level", "weekly", "monthly", "paymentCenter", "choose", "conditionChoose"):
        for row in _info_list(user, target):
            if isinstance(row, dict) and str(row.get("id") or "") == good_id:
                return int(row.get("count") or 0)
    return 0


def record_purchase(user, good_id, count=1):
    good_id = str(good_id or "")
    target = section(good_id)
    info = _info_list(user, target)
    for row in info:
        if isinstance(row, dict) and str(row.get("id") or "") == good_id:
            row["count"] = int(row.get("count") or 0) + int(count)
            return row
    row = {"id": good_id, "count": int(count)}
    info.append(row)
    return row


def set_purchase_count(user, good_id, count):
    good_id = str(good_id or "")
    target = section(good_id)
    info = _info_list(user, target)
    for row in info:
        if isinstance(row, dict) and str(row.get("id") or "") == good_id:
            row["count"] = int(count)
            return row
    row = {"id": good_id, "count": int(count)}
    info.append(row)
    return row


def catalog_rows(limit=None):
    rows = []
    for good_id, item in catalog().items():
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "goodId": good_id,
                "displayName": item.get("displayName") or good_id,
                "price": item.get("price") or 0,
                "currencyUnit": item.get("currencyUnit") or "CASH",
                "items": item.get("items") or [],
                "source": item.get("source") or "catalog",
                "current": good_id in current_goods(),
            }
        )
    rows.sort(key=lambda row: (not row["current"], row["displayName"], row["goodId"]))
    return rows[:limit] if limit else rows


def item_rows(items):
    meta = game_data.item_catalog()
    chars = game_data.character_catalog()
    skins = game_data.skin_catalog()
    avatars = game_data.avatar_catalog()
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        item_type = str(item.get("type") or "")
        info = meta.get(item_id) or {}
        name = info.get("name")
        if not name and item_type == "CHAR":
            name = (chars.get(item_id) or {}).get("name")
        if not name and item_type == "CHAR_SKIN":
            name = (skins.get(item_id) or {}).get("name")
        if not name and item_type == "PLAYER_AVATAR":
            name = (avatars.get(item_id) or {}).get("name")
        rows.append(
            {
                "id": item_id,
                "type": item_type or info.get("type") or "MATERIAL",
                "count": int(item.get("count") or 0),
                "name": name or item_id,
                "rarity": info.get("rarity") or "",
            }
        )
    return rows
