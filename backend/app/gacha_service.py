import copy
import json
import random
import re
import threading
import time
from pathlib import Path

from . import game_data, grants
from .config import BASE_DIR, DATA_DIR


ROOT_DIR = BASE_DIR.parent
POOL_DIR = ROOT_DIR / "server" / "data" / "gacha"
OVERRIDE_PATH = DATA_DIR / "gacha_overrides.json"
_lock = threading.Lock()
_cache = {}

RARITY_ORDER = (5, 4, 3, 2)
RARITY_NAME = {6: "TIER_6", 5: "TIER_5", 4: "TIER_4", 3: "TIER_3", 2: "TIER_2", 1: "TIER_1"}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mtime(path):
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return copy.deepcopy(default)


def _overrides():
    stamp = _mtime(OVERRIDE_PATH)
    with _lock:
        cached = _cache.get("overrides")
        if cached and cached[0] == stamp:
            return copy.deepcopy(cached[1])
        data = _load_json(OVERRIDE_PATH, {"pools": {}})
        if not isinstance(data, dict):
            data = {"pools": {}}
        if not isinstance(data.get("pools"), dict):
            data["pools"] = {}
        _cache["overrides"] = (stamp, data)
        return copy.deepcopy(data)


def _save_overrides(data):
    OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _base_pools():
    table = game_data.excel("gacha_table.json") or {}
    rows = []
    for key in ("gachaPoolClient", "newbeeGachaPoolClient"):
        for row in table.get(key) or []:
            if not isinstance(row, dict) or not row.get("gachaPoolId"):
                continue
            item = copy.deepcopy(row)
            item["source"] = "newbee" if key == "newbeeGachaPoolClient" else "normal"
            rows.append(item)
    return rows


def base_pool(pool_id):
    wanted = str(pool_id or "")
    for row in _base_pools():
        if str(row.get("gachaPoolId")) == wanted:
            return row
    return {}


def _merged_base(pool_id):
    base = base_pool(pool_id)
    override = (_overrides().get("pools") or {}).get(str(pool_id)) or {}
    merged = {}
    merged.update(base)
    if isinstance(override, dict):
        merged.update({key: value for key, value in override.items() if key != "deleted"})
    return merged


def all_pools():
    base = _base_pools()
    overrides = _overrides().get("pools") or {}
    seen = set()
    result = []
    for row in base:
        pool_id = str(row.get("gachaPoolId"))
        seen.add(pool_id)
        merged = dict(row)
        override = overrides.get(pool_id)
        if isinstance(override, dict):
            if override.get("deleted"):
                continue
            merged.update({key: value for key, value in override.items() if key != "deleted"})
            merged["overridden"] = True
        else:
            merged["overridden"] = False
        merged["gachaPoolId"] = pool_id
        result.append(merged)
    for pool_id, override in overrides.items():
        if pool_id in seen or not isinstance(override, dict) or override.get("deleted"):
            continue
        row = copy.deepcopy(override)
        row["gachaPoolId"] = str(pool_id)
        row.setdefault("gachaPoolName", str(pool_id))
        row["custom"] = True
        row["overridden"] = True
        result.append(row)
    result.sort(key=lambda row: (_int(row.get("openTime"), 0), str(row.get("gachaPoolId"))))
    return result


def pool_meta(pool_id):
    row = _merged_base(pool_id)
    if not row:
        return {}
    row.setdefault("gachaPoolName", str(pool_id))
    row.setdefault("enabled", True)
    # The beginner pool stores its discounted per-pull price in gachaPrice
    # (380), while standard pools use 600.  Admin overrides write singlePrice
    # and must take precedence over the static value.
    price = _int(row.get("singlePrice"), 0)
    if price <= 0:
        price = _int(row.get("gachaPrice"), 0)
    if price <= 0:
        price = 600
    row["singlePrice"] = price
    row.setdefault("tenPrice", price * 10)
    row.setdefault("upRate", 0.5)
    row.setdefault("guarantee5", bool(_int(row.get("guarantee5Avail"), 0)))
    row.setdefault("guarantee5Count", _int(row.get("guarantee5Count"), 10))
    return row


def _new_pool_detail(pool_id, meta):
    rates = {
        5: _float(meta.get("rate6"), 0.02),
        4: _float(meta.get("rate5"), 0.08),
        3: _float(meta.get("rate4"), 0.50),
        2: _float(meta.get("rate3"), 0.40),
    }
    total = sum(max(0.0, value) for value in rates.values()) or 1.0
    chars = game_data.character_catalog()
    by_rank = {5: [], 4: [], 3: [], 2: []}
    for char_id, item in chars.items():
        rank = _int(str(item.get("rarity") or "").replace("TIER_", ""), 1) - 1
        rank = max(2, min(5, rank))
        if rank in by_rank:
            by_rank[rank].append(char_id)
    up_chars = [str(value) for value in meta.get("upChars") or [] if value]
    avail = []
    for rank in RARITY_ORDER:
        pool = list(by_rank.get(rank) or [])
        if not pool:
            continue
        avail.append({"rarityRank": rank, "charIdList": pool, "totalPercent": rates[rank] / total})
    up = []
    for rank in (5, 4):
        ids = [char_id for char_id in up_chars if char_id in by_rank.get(rank, [])]
        if ids:
            up.append({"rarityRank": rank, "charIdList": ids, "percent": _float(meta.get("upRate"), 0.5)})
    return {
        "detailInfo": {
            "availCharInfo": {"perAvailList": avail},
            "upCharInfo": {"perCharList": up} if up else None,
            "limitedChar": None,
            "weightUpCharInfoList": None,
            "gachaObjGroups": None,
            "gachaObjList": [
                {"gachaObject": "TEXT", "type": 1, "imageType": 0, "param": str(meta.get("gachaPoolName") or pool_id)},
                {"gachaObject": "AVAIL_CHAR", "type": 0, "imageType": 0, "param": None},
            ],
        },
        "gachaObjGroupType": 0,
        "hasRateUp": bool(up),
        "playerDataDelta": {"modified": {}, "deleted": {}},
    }


def pool_detail(pool_id):
    meta = pool_meta(pool_id)
    if not meta:
        return None
    custom = meta.get("detail")
    if isinstance(custom, dict):
        detail = copy.deepcopy(custom)
    else:
        path = POOL_DIR / f"{pool_id}.json"
        if path.exists():
            detail = _load_json(path, {})
        elif meta.get("basePool"):
            source = POOL_DIR / f"{meta['basePool']}.json"
            detail = _load_json(source, {})
        else:
            detail = {}
    if not detail or not isinstance(detail.get("detailInfo"), dict):
        detail = _new_pool_detail(pool_id, meta)
    detail = copy.deepcopy(detail)
    info = detail.setdefault("detailInfo", {})
    avail_info = info.get("availCharInfo")
    if not isinstance(avail_info, dict):
        avail_info = {"perAvailList": []}
        info["availCharInfo"] = avail_info
    avail = avail_info.get("perAvailList")
    if not isinstance(avail, list) or not avail:
        return _new_pool_detail(pool_id, meta)
    for row in avail:
        rank = _int(row.get("rarityRank"), 0)
        key = {5: "rate6", 4: "rate5", 3: "rate4", 2: "rate3"}.get(rank)
        if key and meta.get(key) is not None:
            row["totalPercent"] = _float(meta.get(key), _float(row.get("totalPercent"), 0.0))
    up_chars = [str(value) for value in meta.get("upChars") or [] if value]
    if up_chars:
        up_info = info.get("upCharInfo")
        if not isinstance(up_info, dict):
            up_info = {"perCharList": []}
            info["upCharInfo"] = up_info
        per = up_info.get("perCharList")
        if not isinstance(per, list):
            per = []
            up_info["perCharList"] = per
        valid = set(game_data.character_catalog())
        up_chars = [char_id for char_id in up_chars if char_id in valid]
        for row in per:
            rank = _int(row.get("rarityRank"), 0)
            if rank == 5:
                row["charIdList"] = up_chars
                row["percent"] = _float(meta.get("upRate"), 0.5)
        if not any(_int(row.get("rarityRank"), 0) == 5 for row in per) and up_chars:
            per.append({"rarityRank": 5, "charIdList": up_chars, "percent": _float(meta.get("upRate"), 0.5)})
        detail["hasRateUp"] = True
    detail.setdefault("playerDataDelta", {"modified": {}, "deleted": {}})
    return detail


def save_pool(pool_id, values, create=False):
    pool_id = str(pool_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", pool_id):
        raise ValueError("卡池 ID 只能包含字母、数字、下划线和连字符")
    if create and pool_meta(pool_id):
        raise ValueError("卡池 ID 已存在")
    if not create and not pool_meta(pool_id):
        raise ValueError("卡池不存在")
    data = _overrides()
    pools = data.setdefault("pools", {})
    current = pools.get(pool_id) if isinstance(pools.get(pool_id), dict) else {}
    payload = dict(current)
    payload.pop("deleted", None)
    payload.update(values)
    payload["gachaPoolId"] = pool_id
    payload["custom"] = bool(create or current.get("custom") or not base_pool(pool_id))
    pools[pool_id] = payload
    _save_overrides(data)
    return payload


def delete_pool(pool_id):
    pool_id = str(pool_id or "")
    data = _overrides()
    pools = data.setdefault("pools", {})
    current = pools.get(pool_id) if isinstance(pools.get(pool_id), dict) else {}
    if not base_pool(pool_id) and current.get("custom"):
        pools.pop(pool_id, None)
    elif base_pool(pool_id) and not current.get("custom"):
        current["enabled"] = False
        pools[pool_id] = current
    else:
        current["deleted"] = True
        pools[pool_id] = current
    _save_overrides(data)
    return True


def _ensure_gacha(user):
    gacha = user.setdefault("gacha", {})
    for key in ("normal", "limit", "single", "special", "fesClassic", "attain", "linkage", "double", "backflow"):
        if not isinstance(gacha.get(key), dict):
            gacha[key] = {}
    if not isinstance(gacha.get("newbee"), dict):
        gacha["newbee"] = {"cnt": 21, "poolId": "BOOT_0_1_3", "openFlag": 1}
    ex = user.setdefault("exgachadata", {})
    if not isinstance(ex, dict):
        user["exgachadata"] = ex = {}
    return gacha


def ensure_gacha_state(user):
    """Give the client every PlayerGacha dictionary it deserializes."""
    _ensure_gacha(user)


def _pity_key(pool_id):
    pool_id = str(pool_id or "")
    if pool_id.startswith("CLASSIC"):
        return "CLASSIC"
    if pool_id.startswith("BOOT"):
        return "BOOT"
    return "NORM"


def _status_key(pool_id, ten=False):
    classic = str(pool_id or "").startswith("CLASSIC")
    if classic:
        return "classicTenGachaTicket" if ten else "classicGachaTicket"
    return "tenGachaTicket" if ten else "gachaTicket"


def _take_status(user, key, amount=1):
    status = user.setdefault("status", {})
    have = _int(status.get(key), 0)
    if have < amount:
        return False
    status[key] = have - amount
    return True


def _take_inventory(user, item_id, amount=1):
    inventory = user.setdefault("inventory", {})
    have = _int(inventory.get(item_id), 0)
    if have < amount:
        return False
    if have == amount:
        inventory.pop(item_id, None)
    else:
        inventory[item_id] = have - amount
    return True


def _take_consumable(user, item_id, amount=1):
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
    if slot["count"] <= 0:
        slot.pop("count", None)
    return True


def _consume(user, key, amount):
    if _take_status(user, key, amount):
        return True
    if _take_inventory(user, key, amount):
        return True
    return _take_consumable(user, key, amount)


def _error(code, message, **extra):
    return {"result": code, "msg": message, "error": message, **extra}


def _rarity_rates(detail, pity):
    info = detail.get("detailInfo") or {}
    avail = ((info.get("availCharInfo") or {}).get("perAvailList")) or []
    rates = {}
    for row in avail:
        rank = _int(row.get("rarityRank"), 0)
        rates[rank] = max(0.0, _float(row.get("totalPercent"), 0.0))
    if 5 in rates:
        rates[5] = max(0.0, rates[5] + max(0, pity - 49) * 0.02)
    total = sum(rates.values())
    if total <= 0:
        return {5: 0.02, 4: 0.08, 3: 0.50, 2: 0.40}, avail
    if abs(total - 1.0) > 1e-9:
        rates = {rank: value / total for rank, value in rates.items()}
    return rates, avail


def _pick_rarity(rates):
    point = random.random()
    running = 0.0
    for rank in RARITY_ORDER:
        running += rates.get(rank, 0.0)
        if point <= running:
            return rank
    return RARITY_ORDER[-1]


def _detail_chars(detail, rarity):
    info = detail.get("detailInfo") or {}
    avail = ((info.get("availCharInfo") or {}).get("perAvailList")) or []
    for row in avail:
        if _int(row.get("rarityRank"), -1) == rarity:
            return [str(value) for value in row.get("charIdList") or [] if value], row
    return [], {}


def _up_chars(detail, rarity):
    info = detail.get("detailInfo") or {}
    up_info = info.get("upCharInfo") or {}
    for row in up_info.get("perCharList") or []:
        if _int(row.get("rarityRank"), -1) == rarity:
            return [str(value) for value in row.get("charIdList") or [] if value], max(0.0, _float(row.get("percent"), 0.0))
    return [], 0.0


def _grant_character(user, char_id, rarity, now):
    chars = user.setdefault("troop", {}).setdefault("chars", {})
    existing = None
    for inst_id, char in chars.items():
        if isinstance(char, dict) and str(char.get("charId")) == char_id:
            existing = (inst_id, char)
            break
    item_get = []
    if existing:
        inst_id, _ = existing
        if rarity >= 5:
            potential = _int(existing[1].get("potentialRank"), 0)
            count = 10 if potential < 5 else 15
            item_get.append({"type": "HGG_SHD", "id": "4004", "count": count})
            status = user.setdefault("status", {})
            status["hggShard"] = _int(status.get("hggShard"), 0) + count
        elif rarity == 4:
            potential = _int(existing[1].get("potentialRank"), 0)
            count = 5 if potential < 5 else 8
            item_get.append({"type": "HGG_SHD", "id": "4004", "count": count})
            status = user.setdefault("status", {})
            status["hggShard"] = _int(status.get("hggShard"), 0) + count
        else:
            count = 30 if rarity == 3 else (5 if rarity == 2 else 1)
            item_get.append({"type": "LGG_SHD", "id": "4005", "count": count})
            status = user.setdefault("status", {})
            status["lggShard"] = _int(status.get("lggShard"), 0) + count
        inventory = user.setdefault("inventory", {})
        inventory[f"p_{char_id}"] = _int(inventory.get(f"p_{char_id}"), 0) + 1
        item_get.append({"type": "MATERIAL", "id": f"p_{char_id}", "count": 1})
        return str(inst_id), False, item_get
    grants.apply_items(user, [{"id": char_id, "type": "CHAR", "count": 1}], source="gacha", now=now)
    new_inst = ""
    for inst_id, char in user.setdefault("troop", {}).setdefault("chars", {}).items():
        if isinstance(char, dict) and str(char.get("charId")) == char_id:
            new_inst = str(inst_id)
            break
    status = user.setdefault("status", {})
    status["hggShard"] = _int(status.get("hggShard"), 0) + 1
    item_get.append({"type": "HGG_SHD", "id": "4004", "count": 1})
    return new_inst, True, item_get


def _pool_state(user, pool_id, count):
    gacha = _ensure_gacha(user)
    pool_id = str(pool_id or "")
    family = "limit" if pool_id.startswith("LIMITED") else ("newbee" if pool_id.startswith("BOOT") else "normal")
    if family == "newbee":
        node = gacha["newbee"]
        node["cnt"] = max(0, _int(node.get("cnt"), 21) - count)
        node.setdefault("poolId", pool_id)
        node["openFlag"] = 1
    else:
        bucket = gacha.setdefault(family, {})
        node = bucket.get(pool_id)
        if not isinstance(node, dict):
            node = {"rarity": 4, "maxCnt": 10, "avail": True, "cnt": 0}
            bucket[pool_id] = node
        node["cnt"] = _int(node.get("cnt"), 0) + count
        node.setdefault("rarity", 4)
        node.setdefault("maxCnt", 10)
        node["avail"] = True
    return gacha


def draw(user, pool_id, count=1, use_tkt=0, item_id="", now=None):
    now = int(now or time.time())
    count = 10 if _int(count, 1) >= 10 else 1
    meta = pool_meta(pool_id)
    if not meta or not meta.get("enabled", True):
        return _error(5771, "卡池不存在或已关闭")
    if str(pool_id or "").startswith("BOOT"):
        gacha = _ensure_gacha(user)
        remaining = _int(gacha["newbee"].get("cnt"), 21)
        if remaining < count:
            return _error(400, "新手池抽卡数不足")
    detail = pool_detail(pool_id)
    if not detail:
        return _error(5771, "卡池详情不可用")
    status = user.setdefault("status", {})
    price = _int(meta.get("tenPrice" if count == 10 else "singlePrice"), 600 * count)
    ticket_key = _status_key(pool_id, ten=(count == 10))
    if count == 10:
        consumed = False
        if _int(use_tkt, 0) in (1, 2, 4):
            if item_id and _consume(user, str(item_id), 1):
                consumed = True
            elif _consume(user, ticket_key, 1):
                consumed = True
        if not consumed:
            if _int(status.get("diamondShard"), 0) < price:
                return _error(5647, "not enough diamond shard")
            status["diamondShard"] = _int(status.get("diamondShard"), 0) - price
    else:
        consumed = False
        if _int(use_tkt, 0) in (1, 2, 4):
            if item_id and _consume(user, str(item_id), 1):
                consumed = True
            elif _consume(user, ticket_key, 1):
                consumed = True
            if not consumed:
                return _error(5771, "gacha_tkt_state_error")
        else:
            if _int(status.get("diamondShard"), 0) < price:
                return _error(5647, "not enough diamond shard")
            status["diamondShard"] = _int(status.get("diamondShard"), 0) - price
    ex = user.setdefault("exgachadata", {})
    pity_key = _pity_key(pool_id)
    pity = _int(ex.get(pity_key), 0)
    rates, _ = _rarity_rates(detail, pity)
    results = []
    hit_high = False
    guarantee_at = max(0, min(count - 1, _int(meta.get("guarantee5Count"), count) - 1))
    for index in range(count):
        rarity = _pick_rarity(rates)
        if index == guarantee_at and not hit_high and _int(meta.get("guarantee5Avail"), 0):
            rarity = 4
        if rarity >= 4:
            hit_high = True
        if rarity == 5:
            pity = 0
        else:
            pity += 1
        chars, _ = _detail_chars(detail, rarity)
        if not chars:
            chars, _ = _detail_chars(detail, 2)
        up, up_rate = _up_chars(detail, rarity)
        if up and random.random() < up_rate:
            char_id = random.choice(up)
        else:
            char_id = random.choice(chars) if chars else "char_123_fang"
        inst_id, is_new, item_get = _grant_character(user, char_id, rarity, now)
        results.append(
            {
                "charId": char_id,
                "rarity": rarity,
                "charInstId": _int(inst_id, 0),
                "isNew": 1 if is_new else 0,
                "itemGet": item_get,
            }
        )
    ex[pity_key] = pity
    gacha = _pool_state(user, pool_id, count)
    attain = gacha.setdefault("attain", {})
    for row in results:
        char_id = str(row["charId"])
        attain[char_id] = _int(attain.get(char_id), 0) + 1
    delta = {
        "deleted": {},
        "modified": {
            key: user.get(key)
            for key in ("status", "troop", "inventory", "consumable", "gacha", "dexNav", "building", "exgachadata")
            if key in user
        },
    }
    response = {"result": 0, "playerDataDelta": delta}
    if count == 1:
        response["charGet"] = results[0]
    else:
        response["gachaResultList"] = results
    return response


def public_recruit_state(user):
    recruit = user.setdefault("recruit", {})
    normal = recruit.setdefault("normal", {})
    slots = normal.setdefault("slots", {})
    for index in range(4):
        node = slots.get(str(index))
        if not isinstance(node, dict):
            slots[str(index)] = {
                "durationInSec": -1,
                "startTs": -1,
                "state": 0,
                "realFinishTs": -1,
                "maxFinishTs": -1,
                "selectTags": [],
                "tags": [],
            }
    return recruit


def start_public_recruit(user, slot_id, tags, duration, now=None):
    now = int(now or time.time())
    recruit = public_recruit_state(user)
    slot = recruit["normal"]["slots"].setdefault(str(slot_id), {})
    if _int(slot.get("state"), 0):
        return _error(1, "recruit slot busy")
    if not _take_status(user, "recruitLicense", 1):
        return _error(1, "not enough recruit license")
    duration = max(0, min(_int(duration, 3600), 32400))
    slot.update(
        {
            "durationInSec": duration,
            "startTs": now,
            "state": 1,
            "realFinishTs": now + duration,
            "maxFinishTs": now + duration + 60,
            "selectTags": [int(value) for value in (tags or []) if _int(value, 0)],
            "tags": [int(value) for value in (tags or []) if _int(value, 0)],
        }
    )
    return {
        "result": 0,
        "playerDataDelta": {"deleted": {}, "modified": {"status": user.get("status", {}), "recruit": recruit}},
    }

