import copy
import time

from . import gift_catalog, grants
from .shop_fixtures import load_shop


SHOP_FILES = {
    "CASH": "CashGoodList.json",
    "LS": "LowGoodList.json",
    "HS": "HighGoodList.json",
    "ES": "ExtraGoodList.json",
    "GP": "GPGoodList.json",
    "LMTGS": "LMTGSGoodList.json",
    "EPGS": "EPGSGoodList.json",
    "REP": "RepGoodList.json",
    "SOCIAL": "SocialGoodList.json",
    "CLASSIC": "ClassicGoodList.json",
    "SKIN": "SkinGoodList.json",
    "FURNI": "FurniGoodList.json",
}

SHOP_CURRENCY = {
    "LS": ("status", "lggShard"),
    "HS": ("status", "hggShard"),
    "ES": ("inventory", "4006"),
    "CLASSIC": ("status", "classicShard"),
    "EPGS": ("inventory", "EPGS_COIN"),
    "SOCIAL": ("status", "socialPoint"),
    "REP": ("inventory", "REP_COIN"),
}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _payload(shop_key):
    filename = SHOP_FILES.get(str(shop_key or "").upper())
    payload = load_shop(filename) if filename else None
    return copy.deepcopy(payload)


def _walk(value):
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def _gp_payload():
    payload = load_shop("GPGoodList.json")
    return copy.deepcopy(payload) if isinstance(payload, dict) else {}


def _iter_packages(payload):
    for group_key in ("weeklyGroup", "monthlyGroup"):
        group = payload.get(group_key) or {}
        for good_id, row in (group.get("packages") or {}).items():
            if isinstance(row, dict):
                yield group_key, str(good_id), row
    for list_key in (
        "monthlySub",
        "levelGP",
        "oneTimeGP",
        "chooseGroup",
        "conditionChooseGroup",
        "condtionTriggerGroup",
        "goodList",
    ):
        for row in payload.get(list_key) or []:
            if isinstance(row, dict) and row.get("goodId"):
                yield list_key, str(row["goodId"]), row


def gp_entry(good_id):
    good_id = str(good_id or "")
    payload = _gp_payload()
    for _, current_id, row in _iter_packages(payload):
        if current_id == good_id:
            return row
    return {}


def good_entry(shop_key, good_id):
    shop_key = str(shop_key or "").upper()
    good_id = str(good_id or "")
    if shop_key == "GP":
        return gp_entry(good_id)
    payload = _payload(shop_key)
    if isinstance(payload, list):
        payload = {"goodList": payload}
    if not isinstance(payload, dict):
        return {}
    for node in _walk(payload):
        if str(node.get("goodId") or "") == good_id:
            return node
    return {}


def _info(user, shop_key):
    shop = user.setdefault("shop", {})
    node = shop.get(shop_key)
    if not isinstance(node, dict):
        node = {}
        shop[shop_key] = node
    info = node.get("info")
    if not isinstance(info, list):
        info = []
        node["info"] = info
    return info


def buy_count(user, shop_key, good_id):
    shop_key = str(shop_key or "").upper()
    good_id = str(good_id or "")
    if shop_key == "GP":
        return gift_catalog.purchase_count(user or {}, good_id)
    for row in _info(user or {}, shop_key):
        if isinstance(row, dict) and str(row.get("id") or "") == good_id:
            return _int(row.get("count"), 0)
    return 0


def record_purchase(user, shop_key, good_id, count=1):
    shop_key = str(shop_key or "").upper()
    good_id = str(good_id or "")
    if shop_key == "GP":
        return gift_catalog.record_purchase(user, good_id, count)
    info = _info(user, shop_key)
    for row in info:
        if isinstance(row, dict) and str(row.get("id") or "") == good_id:
            row["count"] = _int(row.get("count"), 0) + int(count)
            return row
    row = {"id": good_id, "count": int(count)}
    info.append(row)
    return row


def _limit(good):
    if "availCount" in good:
        return _int(good.get("availCount"), -1)
    if "count" in good:
        return _int(good.get("count"), -1)
    return None


def _check_limit(user, shop_key, good, good_id, count=1):
    limit = _limit(good)
    if limit is None or limit < 0:
        return None
    bought = buy_count(user, shop_key, good_id)
    if bought + max(0, count) > limit:
        return f"商品已售罄或超出限购：{good_id}（限购 {limit}，已购 {bought}）"
    return None


def decorate_gp_payload(user):
    payload = _gp_payload()
    for _, good_id, row in _iter_packages(payload):
        items = row.get("items")
        if not isinstance(items, list):
            items = gift_catalog.items(good_id)
        if not items:
            items = gift_catalog.items(good_id)
        row["items"] = items
        row["itemList"] = [dict(item) for item in items]
        row["buyCount"] = buy_count(user, "GP", good_id)
        row.setdefault("useGpTicket", bool(row.get("gpTicketIdList")))
        tickets = row.get("gpTicketIdList") or []
        if tickets and not row.get("gpTicketId"):
            row["gpTicketId"] = str(tickets[0])
        row.setdefault("imgDisplayDataList", [])
    payload["goodList"] = payload.get("goodList") or []
    return payload


def decorate_social_payload(user):
    payload = _payload("SOCIAL") or {}
    state = ((user.get("shop") or {}).get("SOCIAL") or {})
    if isinstance(state.get("charPurchase"), dict):
        payload["charPurchase"] = state["charPurchase"]
    if state.get("costSocialPoint") is not None:
        payload["costSocialPoint"] = _int(state.get("costSocialPoint"), 0)
    payload["creditGroup"] = "creditGroup2" if _int(payload.get("costSocialPoint"), 0) >= 4000 else "creditGroup1"
    payload["playerDataDelta"] = {"deleted": {}, "modified": {}}
    return payload


def purchase_state(user, requested=None):
    """Return remaining purchase counts (official semantics), not bought counts.

    The client treats a missing good as sold out and -1 as unlimited.  Returning
    the bought count here (the old behaviour) made every limited item look as if
    it still had stock, which is why the shop never greyed out after a buy.
    """
    result = {}
    requested = requested if isinstance(requested, dict) else {}
    for raw_key, values in requested.items():
        if not isinstance(values, list):
            continue
        shop_key = str(raw_key or "").upper()
        if shop_key == "GIFT_SHOP":
            shop_key = "GP"
        for raw_id in values:
            good_id = str(raw_id or "")
            if not good_id:
                continue
            good = good_entry(shop_key, good_id)
            if not good:
                continue
            if str(good.get("goodType") or "").upper() == "PROGRESS" or good.get("progressGoodId"):
                progress_id = str(good.get("progressGoodId") or "")
                table = _payload(shop_key) or {}
                levels = (table.get("progressGoodList") or {}).get(progress_id) or []
                node = (((user.get("shop") or {}).get(shop_key) or {}).get("progressInfo") or {}).get(progress_id) or {}
                order = max(1, _int(node.get("order"), 1))
                remaining = len(levels) - order + 1
                if remaining > 0:
                    result[good_id] = remaining
                continue
            limit = _limit(good)
            if limit is None:
                continue
            if limit < 0:
                result[good_id] = -1
                continue
            remaining = limit - buy_count(user, shop_key, good_id)
            if remaining > 0:
                result[good_id] = remaining
    return result


def _wallet(user, source, key):
    node = user.setdefault(source, {})
    return _int(node.get(key), 0)


def _take_wallet(user, source, key, amount):
    node = user.setdefault(source, {})
    have = _int(node.get(key), 0)
    if have < amount:
        return False
    if source == "inventory" and have == amount:
        node.pop(key, None)
    else:
        node[key] = have - amount
    return True


def _take_consumable(user, item_id, amount):
    consumable = user.get("consumable") or {}
    node = consumable.get(str(item_id))
    if not isinstance(node, dict):
        return False
    slot = node.get("0")
    if not isinstance(slot, dict):
        return False
    have = _int(slot.get("count"), 0)
    if have < amount:
        return False
    slot["count"] = have - amount
    if slot["count"] == 0:
        slot.pop("count", None)
    return True


def _deduct_diamond(user, count, platform):
    status = user.setdefault("status", {})
    key = grants.diamond_status_key(user, platform)
    balance = _int(status.get(key), 0)
    if balance < count:
        return False
    status[key] = balance - count
    return True


def _deduct_ticket(user, ticket_id):
    inventory = user.setdefault("inventory", {})
    ticket_id = str(ticket_id or "")
    if not ticket_id:
        return False
    value = inventory.get(ticket_id)
    if isinstance(value, dict):
        value = (value.get("0") or {}).get("count") if isinstance(value.get("0"), dict) else value.get("count")
    if value is not None:
        amount = _int(value, 0)
        if amount > 0:
            if amount == 1:
                inventory.pop(ticket_id, None)
            else:
                inventory[ticket_id] = amount - 1
            return True
    return _take_consumable(user, ticket_id, 1)


def _delta(user, touched=()):
    modified = {key: user.get(key) for key in touched if key in user}
    for key in ("status", "inventory", "shop", "troop", "building", "skin", "dexNav", "consumable", "carousel"):
        if key in user and key in touched:
            modified[key] = user.get(key)
    return {"deleted": {}, "modified": modified}


def _apply(user, items, source, platform, now, touched):
    _, delta = grants.apply_items(user, items, source=source, now=now, platform=platform)
    touched.update((delta.get("modified") or {}).keys())
    return delta


def _error(message, good_id=None):
    result = {"result": 1, "error": message}
    if good_id:
        result["goodId"] = str(good_id)
    return result


def _price_parts(good):
    price = good.get("price")
    if isinstance(price, dict):
        return {
            "item": str(price.get("id") or ""),
            "currency": str(price.get("type") or ""),
            "amount": _int(price.get("count"), 0),
        }
    return {"item": "", "currency": "", "amount": _int(price, 0)}


def _take_price(user, shop_key, good, count, platform):
    price = _price_parts(good)
    if price["item"]:
        amount = price["amount"] * count
        if _take_wallet(user, "inventory", price["item"], amount):
            return None
        if _take_wallet(user, "status", price["item"], amount):
            return None
        return f"{price['item']} 不足，需要 {amount}"
    amount = price["amount"] * count
    if amount <= 0:
        return None
    source_key = SHOP_CURRENCY.get(shop_key)
    if source_key:
        source, key = source_key
        if _take_wallet(user, source, key, amount):
            return None
        return f"{key} 不足，需要 {amount}"
    return f"不支持的价格类型：{good.get('goodId')}"


def _progress_purchase(user, shop_key, good, count, platform, now):
    if count != 1:
        return _error("进度商品一次只能购买 1 个", good.get("goodId"))
    progress_id = str(good.get("progressGoodId") or "")
    table = _payload(shop_key)
    chains = (table or {}).get("progressGoodList") or {}
    levels = chains.get(progress_id)
    if not isinstance(levels, list) or not levels:
        return _error(f"进度商品未配置档位：{progress_id or good.get('goodId')}", good.get("goodId"))
    shop = user.setdefault("shop", {})
    state = shop.setdefault(shop_key, {})
    progress = state.setdefault("progressInfo", {})
    node = progress.get(progress_id)
    if not isinstance(node, dict):
        node = {"count": 0, "order": 1}
        progress[progress_id] = node
    order = max(1, _int(node.get("order"), 1))
    if order > len(levels):
        return _error("该进度奖励已经全部购买", good.get("goodId"))
    level = levels[order - 1] if isinstance(levels[order - 1], dict) else {}
    error = _take_price(user, shop_key, {"price": level.get("price")}, 1, platform)
    if error:
        return _error(error, good.get("goodId"))
    item = level.get("item")
    if not isinstance(item, dict) or not item.get("id"):
        return _error("进度档位缺少奖励物品", good.get("goodId"))
    touched = {"status", "inventory", "shop"}
    _apply(user, [item], "shop-progress", platform, now, touched)
    node["order"] = order + 1
    node["count"] = _int(node.get("count"), 0) + 1
    touched.add("shop")
    return {
        "result": 0,
        "items": [item],
        "goodId": str(good.get("goodId") or ""),
        "playerDataDelta": _delta(user, touched),
    }

def buy_shop_good(user, shop_key, good_id, count=1, platform=None, now=None):
    now = int(now or time.time())
    shop_key = str(shop_key or "").upper()
    good_id = str(good_id or "")
    count = max(1, _int(count, 1))
    good = good_entry(shop_key, good_id)
    if not good:
        return _error("商品不存在", good_id)
    if str(good.get("goodType") or "").upper() == "PROGRESS" or good.get("progressGoodId"):
        return _progress_purchase(user, shop_key, good, count, platform, now)
    item = good.get("item")
    if not isinstance(item, dict) or not item.get("id"):
        return _error("商品没有可发放内容", good_id)
    error = _check_limit(user, shop_key, good, good_id, count)
    if error:
        return _error(error, good_id)
    error = _take_price(user, shop_key, good, count, platform)
    if error:
        return _error(error, good_id)
    items = [dict(item) for _ in range(count)]
    touched = {"status", "inventory", "shop"}
    _apply(user, items, f"shop-{shop_key.lower()}", platform, now, touched)
    record_purchase(user, shop_key, good_id, count)
    if shop_key == "SOCIAL":
        state = user.setdefault("shop", {}).setdefault("SOCIAL", {})
        price = _int(_price_parts(good).get("amount"), 0) * count
        state["costSocialPoint"] = _int(state.get("costSocialPoint"), 0) + price
        import time as _time

        state["curShopId"] = "SOCIAL" + _time.strftime("%Y%m%d", _time.localtime(now))
        if str(item.get("type") or "") == "CHAR":
            purchases = state.setdefault("charPurchase", {})
            char_id = str(item.get("id") or "")
            left = _int(purchases.get(char_id), 1)
            purchases[char_id] = max(0, left - count)
    return {
        "result": 0,
        "items": items,
        "goodId": good_id,
        "playerDataDelta": _delta(user, touched),
    }


def buy_gp(user, good_id, platform=None, now=None):
    now = int(now or time.time())
    good_id = str(good_id or "")
    row = gp_entry(good_id)
    if not row:
        return _error("gift package not found", good_id)
    error = _check_limit(user, "GP", row, good_id, 1)
    if error:
        return _error("purchase limit reached", good_id)
    items = gift_catalog.items(good_id) or list(row.get("items") or [])
    if not items:
        return _error("gift package content not configured", good_id)
    currency = str(row.get("currencyUnit") or "")
    price = _int(row.get("price"), 0)
    if currency == "DIAMOND":
        if not _deduct_diamond(user, price, platform):
            return _error("insufficient originium", good_id)
    elif currency == "CASH":
        return _error("cash order required", good_id)
    elif price > 0:
        return _error(f"unsupported currency: {currency}", good_id)
    touched = {"status", "inventory", "shop"}
    _apply(user, items, "shop-gp", platform, now, touched)
    gift_catalog.record_purchase(user, good_id, 1)
    return {"result": 0, "goodId": good_id, "items": items, "playerDataDelta": _delta(user, touched)}


def buy_gp_with_ticket(user, good_id, ticket_id=None, platform=None, now=None):
    now = int(now or time.time())
    good_id = str(good_id or "")
    row = gp_entry(good_id)
    if not row:
        return _error("gift package not found", good_id)
    error = _check_limit(user, "GP", row, good_id, 1)
    if error:
        return _error("purchase limit reached", good_id)
    tickets = [str(value) for value in row.get("gpTicketIdList") or [] if value]
    ticket_id = str(ticket_id or (tickets[0] if tickets else ""))
    if tickets and ticket_id not in tickets:
        return _error("ticket not accepted", good_id)
    if not _deduct_ticket(user, ticket_id):
        return _error("ticket not found", good_id)
    items = gift_catalog.items(good_id) or list(row.get("items") or [])
    touched = {"status", "inventory", "shop", "consumable"}
    _apply(user, items, "shop-gp-ticket", platform, now, touched)
    gift_catalog.record_purchase(user, good_id, 1)
    return {"result": 0, "goodId": good_id, "items": items, "playerDataDelta": _delta(user, touched)}


def buy_skin(user, good_id, platform=None, now=None):
    now = int(now or time.time())
    payload = _payload("SKIN") or {}
    entries = payload.get("goodList") if isinstance(payload, dict) else payload
    entries = entries or []
    good = next((row for row in entries if isinstance(row, dict) and str(row.get("goodId")) == str(good_id)), None)
    if not good:
        for row in entries:
            if isinstance(row, dict) and str(row.get("goodId", "")).startswith(str(good_id)):
                good = row
                break
    if not good:
        return _error("皮肤商品不存在", good_id)
    skin_id = str(good.get("skinId") or "")
    price = _int(good.get("price"), 0)
    skin = user.setdefault("skin", {})
    owned = skin.setdefault("characterSkins", {})
    if skin_id in owned:
        return _error("已经拥有该皮肤", good_id)
    if not _deduct_diamond(user, price, platform):
        return _error("源石不足", good_id)
    owned[skin_id] = 1
    skin.setdefault("skinTs", {})[skin_id] = now
    record_purchase(user, "CASH", str(good.get("goodId") or good_id), 1)
    return {
        "result": 0,
        "goodId": str(good.get("goodId") or good_id),
        "skinId": skin_id,
        "items": [{"id": skin_id, "type": "CHAR_SKIN", "count": 1}],
        "playerDataDelta": _delta(user, {"status", "skin", "shop"}),
    }


def _furni_goods():
    payload = _payload("FURNI") or {}
    goods = {}
    for row in payload.get("goods") or []:
        if isinstance(row, dict) and row.get("goodId"):
            goods[str(row["goodId"])] = row
    return goods


def _take_furni_price(user, total_coin, total_dia, platform, cost_type=""):
    if total_dia > 0 and (str(cost_type).upper() in {"DIAMOND", "DIA", "ORIGINIUM"} or total_coin <= 0):
        return None if _deduct_diamond(user, total_dia, platform) else "源石不足"
    if total_coin > 0:
        return None if _take_wallet(user, "inventory", "3401", total_coin) else "家具零件不足"
    return None


def buy_furni(user, good_id, buy_count=1, cost_type="", platform=None, now=None):
    now = int(now or time.time())
    good_id = str(good_id or "")
    buy_count = max(1, _int(buy_count, 1))
    good = _furni_goods().get(good_id)
    if not good or not good.get("furniId"):
        return _error("未知家具商品", good_id)
    error = _check_limit(user, "FURNI", good, good_id, buy_count)
    if error:
        return _error(error, good_id)
    total_coin = _int(good.get("priceCoin"), 0) * buy_count
    total_dia = _int(good.get("priceDia"), 0) * buy_count
    error = _take_furni_price(user, total_coin, total_dia, platform, cost_type)
    if error:
        return _error(error, good_id)
    items = [{"id": str(good["furniId"]), "type": "FURN", "count": buy_count}]
    touched = {"status", "inventory", "building", "shop"}
    _apply(user, items, "shop-furni", platform, now, touched)
    record_purchase(user, "FURNI", good_id, buy_count)
    return {"result": 0, "goodId": good_id, "items": items, "playerDataDelta": _delta(user, touched)}


def buy_furni_group(user, group_id, goods, cost_type="", platform=None, now=None):
    now = int(now or time.time())
    if not isinstance(goods, list) or not goods:
        return _error("goods 不能为空")
    catalog = _furni_goods()
    total_coin = 0
    total_dia = 0
    items = []
    records = []
    for raw in goods:
        if not isinstance(raw, dict):
            continue
        good_id = str(raw.get("id") or raw.get("goodId") or "")
        count = max(1, _int(raw.get("count"), 1))
        good = catalog.get(good_id)
        if not good or not good.get("furniId"):
            return _error(f"未知家具商品：{good_id}", good_id)
        error = _check_limit(user, "FURNI", good, good_id, count)
        if error:
            return _error(error, good_id)
        total_coin += _int(good.get("priceCoin"), 0) * count
        total_dia += _int(good.get("priceDia"), 0) * count
        items.append({"id": str(good["furniId"]), "type": "FURN", "count": count})
        records.append((good_id, count))
    error = _take_furni_price(user, total_coin, total_dia, platform, cost_type)
    if error:
        return _error(error)
    touched = {"status", "inventory", "building", "shop", "carousel"}
    _apply(user, items, "shop-furni-group", platform, now, touched)
    for good_id, count in records:
        record_purchase(user, "FURNI", good_id, count)
    carousel = user.setdefault("carousel", {})
    furniture = carousel.setdefault("furnitureShop", {})
    furniture.setdefault("groups", {})[str(group_id or "")] = 1
    return {
        "result": 0,
        "groupId": str(group_id or ""),
        "items": items,
        "playerDataDelta": _delta(user, touched),
    }
