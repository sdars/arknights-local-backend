import copy
import json
import threading
from pathlib import Path

from .config import BASE_DIR, DATA_DIR


BASE_PATH = BASE_DIR / "fixtures" / "product_list.json"
OVERRIDE_PATH = DATA_DIR / "product_overrides.json"
_lock = threading.Lock()
_cache = {}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return copy.deepcopy(default)


def _mtime(path):
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def _load_base():
    key = "base"
    stamp = _mtime(BASE_PATH)
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] == stamp:
            return copy.deepcopy(cached[1])
        payload = _read(BASE_PATH, {"productList": []})
        _cache[key] = (stamp, payload)
        return copy.deepcopy(payload)


def _load_overrides():
    key = "overrides"
    stamp = _mtime(OVERRIDE_PATH)
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] == stamp:
            return copy.deepcopy(cached[1])
        payload = _read(OVERRIDE_PATH, {"products": {}})
        if not isinstance(payload, dict):
            payload = {"products": {}}
        if not isinstance(payload.get("products"), dict):
            payload["products"] = {}
        _cache[key] = (stamp, payload)
        return copy.deepcopy(payload)


def _save_overrides(payload):
    OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    stamp = _mtime(OVERRIDE_PATH)
    with _lock:
        _cache["overrides"] = (stamp, copy.deepcopy(payload))


def all_products(include_disabled=True):
    base = _load_base().get("productList") or []
    overrides = _load_overrides().get("products") or {}
    merged = []
    seen = set()
    for raw in base:
        if not isinstance(raw, dict):
            continue
        product = copy.deepcopy(raw)
        product_id = str(product.get("product_id") or "")
        if not product_id:
            continue
        change = overrides.get(product_id) or {}
        if isinstance(change, dict):
            product.update({key: value for key, value in change.items() if key != "deleted"})
        if change.get("deleted"):
            continue
        seen.add(product_id)
        if include_disabled or _int(product.get("status"), 1) == 1:
            merged.append(product)
    for product_id, change in overrides.items():
        if product_id in seen or not isinstance(change, dict) or change.get("deleted"):
            continue
        if not change.get("product_id") and not change.get("appstore_id"):
            continue
        product = copy.deepcopy(change)
        product.setdefault("product_id", product_id)
        product.setdefault("appstore_id", product.get("product_id"))
        product.setdefault("name", product.get("product_id"))
        product.setdefault("price", 0)
        product.setdefault("status", 1)
        product.setdefault("platform", 2)
        product.setdefault("type", 0)
        product.setdefault("app_id", 1)
        product.setdefault("channel_id", 1)
        product.setdefault("world_id", 0)
        product.setdefault("desc", "")
        product.setdefault("extra_data", "")
        product.setdefault("channel_product_code", None)
        product.setdefault("channel_product_id", product.get("appstore_id"))
        if include_disabled or _int(product.get("status"), 1) == 1:
            merged.append(product)
    merged.sort(key=lambda item: (_int(item.get("store_id"), 0), str(item.get("product_id") or "")))
    return merged


def active_products():
    return all_products(include_disabled=False)


def find_product(store_id=None, good_id=None, include_disabled=False):
    products = all_products(include_disabled=include_disabled)
    if store_id is not None:
        try:
            wanted = int(store_id)
        except (TypeError, ValueError):
            wanted = None
        if wanted is not None:
            for item in products:
                if _int(item.get("store_id"), -1) == wanted:
                    return item
    text = str(good_id or "").strip()
    if text:
        for item in products:
            if text in {str(item.get("product_id") or ""), str(item.get("appstore_id") or "")}:
                return item
        for item in products:
            if text == str(item.get("channel_product_id") or ""):
                return item
    return None


def next_store_id():
    used = [_int(item.get("store_id"), 0) for item in all_products(include_disabled=True)]
    return max(used or [5400]) + 1


def _normalize_product(values, existing=None):
    product = copy.deepcopy(existing or {})
    product_id = str(values.get("product_id") or product.get("product_id") or "").strip()
    if not product_id:
        raise ValueError("商品 ID 不能为空")
    appstore_id = str(values.get("appstore_id") or product.get("appstore_id") or product_id).strip()
    product.update(
        {
            "product_id": product_id,
            "appstore_id": appstore_id,
            "name": str(values.get("name") or product.get("name") or product_id).strip(),
            "desc": str(values.get("desc") or product.get("desc") or ""),
            "price": max(0, _int(values.get("price"), _int(product.get("price"), 0))),
            "status": 1 if str(values.get("status", product.get("status", 1))) in {"1", "true", "on"} else 0,
            "store_id": _int(values.get("store_id"), _int(product.get("store_id"), 0)),
            "platform": _int(values.get("platform"), _int(product.get("platform"), 2)),
            "type": _int(values.get("type"), _int(product.get("type"), 0)),
            "channel_product_id": str(values.get("channel_product_id") or appstore_id),
            "grant_good_id": str(values.get("grant_good_id") or product.get("grant_good_id") or ""),
        }
    )
    if "grant_items" in values:
        items = values.get("grant_items")
        product["grant_items"] = copy.deepcopy(items) if isinstance(items, list) else []
    if product["store_id"] <= 0:
        product["store_id"] = next_store_id()
    product.setdefault("app_id", 1)
    product.setdefault("channel_id", 1)
    product.setdefault("world_id", 0)
    product.setdefault("extra_data", "")
    product.setdefault("channel_product_code", None)
    return product


def save_product(values, original_id=None):
    overrides = _load_overrides()
    products = overrides.setdefault("products", {})
    existing = None
    if original_id:
        existing = find_product(good_id=original_id, include_disabled=True)
        if existing is None:
            existing = products.get(str(original_id))
    elif values.get("product_id"):
        existing = find_product(good_id=values.get("product_id"), include_disabled=True)
    product = _normalize_product(values, existing if isinstance(existing, dict) else None)
    product_id = product["product_id"]
    for other in all_products(include_disabled=True):
        other_id = str(other.get("product_id") or "")
        if other_id == product_id or (original_id and other_id == str(original_id)):
            continue
        if _int(other.get("store_id"), -1) == _int(product.get("store_id"), -2):
            raise ValueError(f"store_id 已被 {other_id} 占用")
        if str(other.get("appstore_id") or "") == product["appstore_id"]:
            raise ValueError(f"appstore_id 已被 {other_id} 占用")
    if original_id and str(original_id) != product_id:
        previous = products.get(str(original_id))
        if isinstance(previous, dict):
            previous["deleted"] = True
            products[str(original_id)] = previous
    products[product_id] = product
    _save_overrides(overrides)
    return product


def delete_product(product_id):
    overrides = _load_overrides()
    products = overrides.setdefault("products", {})
    product_id = str(product_id or "")
    base_ids = {
        str(item.get("product_id") or "")
        for item in (_load_base().get("productList") or [])
        if isinstance(item, dict) and item.get("product_id")
    }
    if product_id not in base_ids:
        products.pop(product_id, None)
        _save_overrides(overrides)
        return True
    existing = find_product(good_id=product_id, include_disabled=True) or products.get(product_id)
    if isinstance(existing, dict):
        existing = dict(existing)
        existing["status"] = 0
        existing.pop("deleted", None)
        products[product_id] = existing
    else:
        products[product_id] = {"product_id": product_id, "status": 0}
    _save_overrides(overrides)
    return True


def set_status(product_id, status):
    product = find_product(good_id=product_id, include_disabled=True)
    if not product:
        return False
    values = dict(product)
    values["status"] = 1 if status else 0
    save_product(values, original_id=product_id)
    return True

