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
EXTRA_POOL_DIR = DATA_DIR / "gacha_details"
EXTRA_POOL_PATH = DATA_DIR / "gacha_pool_extras.json"
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
    # File timestamps can have a coarse granularity on some filesystems.  Do
    # not let a same-tick write keep serving the previous (for example a
    # tombstoned) override table from the in-process cache.
    stamp = _mtime(OVERRIDE_PATH)
    with _lock:
        _cache["overrides"] = (stamp, copy.deepcopy(data))


def _extra_pool_rows():
    """New client pools that may not exist in the installed static table yet.

    ``server/data`` is runtime data and is not tracked by git.  Keeping a
    small overlay in ``backend/data`` means a fresh deployment still knows
    about pools shipped by a newer client resource version.
    """
    stamp = _mtime(EXTRA_POOL_PATH)
    with _lock:
        cached = _cache.get("extra_pools")
        if cached and cached[0] == stamp:
            return copy.deepcopy(cached[1])
        payload = _load_json(EXTRA_POOL_PATH, {"gachaPoolClient": []})
        rows = payload.get("gachaPoolClient") if isinstance(payload, dict) else []
        rows = [copy.deepcopy(row) for row in (rows or []) if isinstance(row, dict) and row.get("gachaPoolId")]
        _cache["extra_pools"] = (stamp, rows)
        return copy.deepcopy(rows)


def _base_pools():
    table = game_data.excel("gacha_table.json") or {}
    rows = []
    seen = set()
    for key in ("gachaPoolClient", "newbeeGachaPoolClient"):
        for row in table.get(key) or []:
            if not isinstance(row, dict) or not row.get("gachaPoolId"):
                continue
            item = copy.deepcopy(row)
            item["source"] = "newbee" if key == "newbeeGachaPoolClient" else "normal"
            rows.append(item)
            seen.add(str(item["gachaPoolId"]))
    # A newer client can expose pools that are absent from the installed
    # resource table.  Overlay rows take precedence, but never duplicate a
    # pool id.
    for row in _extra_pool_rows():
        item = copy.deepcopy(row)
        item["source"] = "normal"
        pool_id = str(item["gachaPoolId"])
        if pool_id in seen:
            for index, existing in enumerate(rows):
                if str(existing.get("gachaPoolId")) == pool_id:
                    rows[index] = item
                    break
        else:
            rows.append(item)
            seen.add(pool_id)
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
    up5_chars = [str(value) for value in meta.get("up5Chars") or [] if value]
    avail = []
    for rank in RARITY_ORDER:
        pool = list(by_rank.get(rank) or [])
        if not pool:
            continue
        avail.append({"rarityRank": rank, "charIdList": pool, "totalPercent": rates[rank] / total})
    up = []
    for rank, candidates, rate_key in (
        (5, up_chars, "upRate"),
        (4, up5_chars, "up5Rate"),
    ):
        ids = [char_id for char_id in candidates if char_id in by_rank.get(rank, [])]
        if ids:
            up.append(
                {
                    "rarityRank": rank,
                    "charIdList": ids,
                    "percent": _float(meta.get(rate_key), _float(meta.get("upRate"), 0.5)),
                }
            )
    # ``rateUp`` lets a pool show its featured operators without applying a
    # separate weight-up (some event pools list the selectable operators only).
    # The admin editor uses the same flag for custom pools.
    rate_up = bool(up) if meta.get("rateUp") is None else bool(meta.get("rateUp"))
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
        "hasRateUp": rate_up,
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
        if not path.exists():
            path = EXTRA_POOL_DIR / f"{pool_id}.json"
        if path.exists():
            detail = _load_json(path, {})
        elif meta.get("basePool"):
            source = POOL_DIR / f"{meta['basePool']}.json"
            if not source.exists():
                source = EXTRA_POOL_DIR / f"{meta['basePool']}.json"
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
    up5_chars = [str(value) for value in meta.get("up5Chars") or [] if value]
    # A saved admin pool always carries upChars/up5Chars, including empty
    # lists, so an empty value means "clear the UP list" rather than "keep the
    # base pool's list".
    has_up_config = "upChars" in meta or "up5Chars" in meta
    if has_up_config:
        valid = set(game_data.character_catalog())
        up_chars = [char_id for char_id in up_chars if char_id in valid]
        up5_chars = [char_id for char_id in up5_chars if char_id in valid]
        rows = []
        if up_chars:
            rows.append({"rarityRank": 5, "charIdList": up_chars, "percent": _float(meta.get("upRate"), 0.5)})
        if up5_chars:
            rows.append(
                {
                    "rarityRank": 4,
                    "charIdList": up5_chars,
                    "percent": _float(meta.get("up5Rate"), _float(meta.get("upRate"), 0.5)),
                }
            )
        info["upCharInfo"] = {"perCharList": rows} if rows else None
        detail["hasRateUp"] = bool(rows) if meta.get("rateUp") is None else bool(meta.get("rateUp"))
    elif meta.get("rateUp") is not None:
        detail["hasRateUp"] = bool(meta.get("rateUp"))
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


def _pool_rule(pool_id):
    return str((pool_meta(pool_id) or {}).get("gachaRuleType") or "")


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
    gacha = _ensure_gacha(user)
    double_bucket = gacha.setdefault("double", {})
    normal_bucket = gacha.get("normal")
    if isinstance(normal_bucket, dict):
        for pool_id, old in list(normal_bucket.items()):
            if _pool_rule(pool_id) not in ("DOUBLE", "CLASSIC_DOUBLE"):
                continue
            if not isinstance(old, dict):
                normal_bucket.pop(pool_id, None)
                continue
            # Older builds stored the DOUBLE show counter in the ``normal``
            # bucket and deleted it on every sync.  Keep the public guarantee
            # node the client needs for "N pulls until a 5-star", and move
            # the cumulative counter to the real ``double`` bucket.
            legacy_show = max(0, _int(old.get("showCnt"), _int(old.get("cnt"), 0)))
            if pool_id not in double_bucket:
                double_bucket[pool_id] = {
                    "showCnt": legacy_show,
                    "hitCharState": 0,
                    "hitCharId": "",
                }
            if "avail" not in old:
                max_cnt = max(1, _int(pool_meta(pool_id).get("guarantee5Count"), 10))
                old["maxCnt"] = max_cnt
                old["avail"] = legacy_show < max_cnt
                old["cnt"] = legacy_show if legacy_show < max_cnt else 0
                old.setdefault("rarity", 4)
    for pool_id, node in list(double_bucket.items()):
        if not isinstance(node, dict):
            double_bucket[pool_id] = {"showCnt": 0, "hitCharState": 0, "hitCharId": ""}
            continue
        node["showCnt"] = max(0, _int(node.get("showCnt"), 0))
        state = _int(node.get("hitCharState"), 0)
        node["hitCharState"] = state if state in (0, 1, 2) else 0
        node["hitCharId"] = str(node.get("hitCharId") or "")
    # Older server builds used ``gacha.attain`` as a character-id counter.
    # The client schema is Dictionary<poolId, {attain6Count}>; leaving the
    # integer values there breaks PlayerGacha deserialization and can make the
    # whole recruit page unavailable.  Preserve the old counters privately.
    attain_bucket = gacha.get("attain")
    if isinstance(attain_bucket, dict):
        legacy = user.setdefault("exgachadata", {}).setdefault("attainChars", {})
        if not isinstance(legacy, dict):
            legacy = {}
            user["exgachadata"]["attainChars"] = legacy
        for key, node in list(attain_bucket.items()):
            if isinstance(node, int):
                legacy[str(key)] = _int(legacy.get(str(key)), 0) + max(0, node)
                attain_bucket.pop(key, None)
            elif not isinstance(node, dict):
                attain_bucket.pop(key, None)
            else:
                node["attain6Count"] = max(0, _int(node.get("attain6Count"), 0))
    single_bucket = gacha.get("single")
    if isinstance(single_bucket, dict):
        for node in single_bucket.values():
            if not isinstance(node, dict):
                continue
            node["maxCnt"] = max(1, _int(node.get("maxCnt"), 10))
            node["cnt"] = max(0, min(_int(node.get("cnt"), 0), node["maxCnt"] - 1))
            node["avail"] = bool(node.get("avail", True))
            node["singleEnsureCnt"] = max(0, _int(node.get("singleEnsureCnt"), 0))
            node["singleEnsureUse"] = bool(node.get("singleEnsureUse", False))
            node["singleEnsureChar"] = str(node.get("singleEnsureChar") or "")
    limit_bucket = gacha.get("limit")
    if isinstance(limit_bucket, dict):
        for node in limit_bucket.values():
            if not isinstance(node, dict):
                continue
            node["leastFree"] = max(0, _int(node.get("leastFree"), 0))
            node["poolCnt"] = max(0, _int(node.get("poolCnt"), 0))
            node["recruitedFreeChar"] = bool(node.get("recruitedFreeChar", False))
    # Accounts created before the guarantee node was written for DOUBLE or
    # LIMITED banners only have the rule-specific counter.  Once that counter
    # reaches the one-time guarantee length, publish the consumed node so the
    # client stops falling back to the static "10 pulls" text.
    if isinstance(normal_bucket, dict):
        for family, counter_key in (("double", "showCnt"), ("limit", "poolCnt")):
            bucket = gacha.get(family)
            if not isinstance(bucket, dict):
                continue
            for pool_id, node in bucket.items():
                if pool_id in normal_bucket or not isinstance(node, dict):
                    continue
                meta = pool_meta(pool_id)
                if not _int(meta.get("guarantee5Avail"), 0):
                    continue
                max_cnt = max(1, _int(meta.get("guarantee5Count"), 10))
                total = max(0, _int(node.get(counter_key), 0))
                if total >= max_cnt:
                    normal_bucket[pool_id] = {
                        "rarity": 4,
                        "maxCnt": max_cnt,
                        "avail": False,
                        "cnt": 0,
                    }
    for family in ("fesClassic", "special", "backflow"):
        bucket = gacha.get(family)
        if not isinstance(bucket, dict):
            continue
        for pool_id, node in bucket.items():
            if not isinstance(node, dict):
                bucket[pool_id] = {"upChar": {}}
            elif not isinstance(node.get("upChar"), dict):
                node["upChar"] = {}
    # Repair the old cumulative counter bug.  The client renders this field as
    # ``maxCnt - cnt`` ("N pulls until a 5-star"); values >= maxCnt therefore
    # overflow into negative UI such as "-10次内".
    for family in ("normal",):
        bucket = gacha.get(family)
        if not isinstance(bucket, dict):
            continue
        for pool_id, node in bucket.items():
            if not isinstance(node, dict):
                continue
            meta = pool_meta(pool_id)
            max_cnt = max(1, _int(meta.get("guarantee5Count"), 10))
            available = bool(_int(meta.get("guarantee5Avail"), 0)) and bool(node.get("avail", True))
            cnt = max(0, _int(node.get("cnt"), 0))
            if not available or cnt >= max_cnt:
                # A completed guarantee cycle must not be carried into the
                # next banner cycle as a larger counter.
                available = False
                cnt = 0
            node["maxCnt"] = max_cnt
            node["avail"] = available
            node["cnt"] = cnt


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


ITEM_STATUS_KEYS = {
    "4003": "diamondShard",
    "7003": "gachaTicket",
    "7004": "tenGachaTicket",
    "classic_gacha": "classicGachaTicket",
    "classic_gacha_10": "classicTenGachaTicket",
}


def _normalize_item_list(item_list):
    rows = []
    for raw in item_list or []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or raw.get("itemId") or "")
        count = _int(raw.get("count"), 0)
        if not item_id or count <= 0:
            return []
        rows.append((item_id, count))
    return rows


def _take_item(user, item_id, amount=1):
    """Consume a client item id from the same stores the client reads.

    Normal permits live in ``status``.  Event permits and other voucher-like
    rewards are represented in both ``inventory`` and ``consumable``; keep
    those two representations in sync while consuming either one.
    """
    item_id = str(item_id or "")
    amount = max(0, _int(amount, 0))
    if not item_id or amount <= 0:
        return False
    status_key = ITEM_STATUS_KEYS.get(item_id)
    if status_key:
        return _take_status(user, status_key, amount)
    if _take_status(user, item_id, amount):
        return True

    inventory = user.get("inventory") or {}
    consumable = user.get("consumable") or {}
    inv_have = _int(inventory.get(item_id), 0)
    slot = ((consumable.get(item_id) or {}).get("0") or {}) if isinstance(consumable, dict) else {}
    con_have = _int(slot.get("count"), 0) if isinstance(slot, dict) else 0
    if max(inv_have, con_have) < amount:
        return False

    # If inventory carries the item, inventory is authoritative.  The
    # consumable entry is a second view of the same stack and must move with
    # it.  When only the consumable view exists, use that instead.
    if inv_have >= amount:
        _take_inventory(user, item_id, amount)
        if con_have:
            _take_consumable(user, item_id, min(amount, con_have))
        return True
    if con_have >= amount:
        _take_consumable(user, item_id, amount)
        if inv_have:
            _take_inventory(user, item_id, min(amount, inv_have))
        return True
    return False


def _consume_item_list(user, item_list):
    rows = _normalize_item_list(item_list)
    if not rows:
        return False, []
    for item_id, count in rows:
        if not _take_item(user, item_id, count):
            return False, rows
    return True, rows


def _charge_diamond_shard(user, amount):
    status = user.setdefault("status", {})
    amount = max(0, _int(amount, 0))
    if _int(status.get("diamondShard"), 0) < amount:
        return False
    status["diamondShard"] = _int(status.get("diamondShard"), 0) - amount
    return True


def _ticket_keys(pool_id):
    classic = str(pool_id or "").startswith("CLASSIC")
    if classic:
        return "classicGachaTicket", "classicTenGachaTicket", "classic_gacha", "classic_gacha_10"
    return "gachaTicket", "tenGachaTicket", "7003", "7004"


def _charge_gacha(user, pool_id, count, use_tkt, item_id, item_list, price):
    """Charge one advanced draw without silently changing the requested type.

    The client uses every value of ``Torappu.GachaType``.  Treating only
    1/2/4 as tickets (the previous behaviour) made a ten-pull permit look like
    a failed Diamond pull.  Every explicit ticket type is now either consumed
    or rejected; it never falls back to Orundum.
    """
    use_tkt = _int(use_tkt, 0)
    if use_tkt < 0:
        use_tkt = 0
    _single_key, _ten_key, single_item, ten_item = _ticket_keys(pool_id)
    rows = _normalize_item_list(item_list)

    if use_tkt == 0:
        if not _charge_diamond_shard(user, price):
            return _error(5647, "not enough diamond shard")
        return None

    if count == 1:
        if use_tkt in (1, 6):
            requested = str(item_id or "")
            ticket_item = requested if requested in {"7003", "classic_gacha"} else single_item
            if not _take_item(user, ticket_item, 1):
                return _error(5771, "gacha_tkt_state_error")
            return None
        if use_tkt == 3:
            if item_id and _take_item(user, item_id, 1):
                return None
            limit = ((user.get("gacha") or {}).get("limit") or {}).get(str(pool_id))
            if isinstance(limit, dict) and _int(limit.get("leastFree"), 0) > 0:
                limit["leastFree"] = _int(limit.get("leastFree"), 0) - 1
                return None
            return _error(5771, "gacha_tkt_state_error")
        if use_tkt == 4:
            if not item_id or not _take_item(user, item_id, 1):
                return _error(5771, "gacha_tkt_state_error")
            return None
        return _error(5771, "gacha_tkt_state_error")

    # Ten-pull types: 2=TenTicket, 5=10xSingleTicket,
    # 7=ClassicTenTicket, 8=10xClassicSingleTicket, 9=CombineTenTicket.
    if use_tkt in (2, 7):
        requested = str(item_id or "")
        ticket_item = requested if requested in {"7004", "classic_gacha_10"} else ten_item
        if not _take_item(user, ticket_item, 1):
            return _error(5771, "gacha_tkt_state_error")
        return None
    if use_tkt in (5, 8):
        requested = str(item_id or "")
        ticket_item = requested if requested in {"7003", "classic_gacha"} else single_item
        if not _take_item(user, ticket_item, 10):
            return _error(5771, "gacha_tkt_state_error")
        return None
    if use_tkt == 9:
        if not rows:
            return _error(5771, "gacha_tkt_state_error")
        ok, rows = _consume_item_list(user, item_list)
        if not ok:
            return _error(5771, "gacha_tkt_state_error")
        # Prevent a malformed/truncated combine list from buying a ten-pull
        # for a token amount.  Event-specific permits are intentionally
        # treated as full-value because their ids are dynamic.
        value = 0
        full_value = False
        for item_id, amount in rows:
            if item_id == "4003":
                value += amount
            elif item_id == "7003" or item_id == "classic_gacha":
                value += amount * max(1, _int(pool_meta(pool_id).get("singlePrice"), 600))
            elif item_id == "7004" or item_id == "classic_gacha_10":
                full_value = True
            else:
                full_value = True
        if not full_value and value < price:
            return _error(5771, "gacha_tkt_state_error")
        return None
    return _error(5771, "gacha_tkt_state_error")


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


def _guarantee_bucket(gacha, rule):
    # SINGLE pools expose their own counter class; every other pool type uses
    # PlayerGacha.PlayerGachaPool in ``gacha.normal`` for the one-time
    # "within N pulls" 5-star guarantee.
    return gacha.setdefault("single" if rule == "SINGLE" else "normal", {})


def _guarantee_node(user, pool_id, meta, create=False):
    gacha = _ensure_gacha(user)
    rule = str(meta.get("gachaRuleType") or "")
    if rule == "BOOT" or str(pool_id or "").startswith("BOOT"):
        return None, 0, False, 0
    if not _int(meta.get("guarantee5Avail"), 0):
        return None, 0, False, 0
    bucket = _guarantee_bucket(gacha, rule)
    node = bucket.get(str(pool_id))
    if not isinstance(node, dict):
        if not create:
            return None, max(1, _int(meta.get("guarantee5Count"), 10)), False, 0
        node = {
            "rarity": 4,
            "maxCnt": max(1, _int(meta.get("guarantee5Count"), 10)),
            "avail": True,
            "cnt": 0,
        }
        if rule == "SINGLE":
            node.update(
                {
                    "singleEnsureCnt": 0,
                    "singleEnsureUse": False,
                    "singleEnsureChar": "",
                }
            )
        bucket[str(pool_id)] = node
    max_cnt = max(1, _int(node.get("maxCnt"), _int(meta.get("guarantee5Count"), 10)))
    available = bool(node.get("avail", True))
    cnt = max(0, _int(node.get("cnt"), 0))
    return node, max_cnt, available, cnt


def _guarantee_force_index(user, pool_id, meta, count):
    """Return the pull index that must be a 5-star, or -1.

    The guarantee is shared by single and ten-pull requests: the N-th pull
    since the banner started must be a 5-star even when the player buys ten
    singles one at a time.
    """
    node, max_cnt, available, cnt = _guarantee_node(user, pool_id, meta)
    if not _int(meta.get("guarantee5Avail"), 0):
        return -1
    if node is None:
        max_cnt = max(1, _int(meta.get("guarantee5Count"), 10))
        return count - 1 if count >= max_cnt else -1
    if not available:
        return -1
    threshold = max_cnt - cnt
    if 1 <= threshold <= count:
        return threshold - 1
    return -1


def _update_guarantee(user, pool_id, meta, count, results=None):
    node, max_cnt, available, cnt = _guarantee_node(user, pool_id, meta, create=True)
    if node is None:
        return None
    if available:
        if results:
            for row in results:
                if _int(row.get("rarity"), 0) >= 4:
                    available = False
                    cnt = 0
                    break
                cnt += 1
        elif count:
            cnt += count
        if cnt >= max_cnt:
            available = False
            cnt = 0
    else:
        cnt = 0
    node["cnt"] = cnt
    node["avail"] = available
    node["maxCnt"] = max_cnt
    node.setdefault("rarity", 4)
    return node


def _pool_state(user, pool_id, count, results=None):
    gacha = _ensure_gacha(user)
    pool_id = str(pool_id or "")
    meta = pool_meta(pool_id)
    rule = str(meta.get("gachaRuleType") or "")
    if rule == "BOOT" or pool_id.startswith("BOOT"):
        node = gacha["newbee"]
        node["cnt"] = max(0, _int(node.get("cnt"), 21) - count)
        node.setdefault("poolId", pool_id)
        node["openFlag"] = 1
        return gacha
    guarantee_node = _update_guarantee(user, pool_id, meta, count, results)
    if rule in ("DOUBLE", "CLASSIC_DOUBLE"):
        bucket = gacha.setdefault("double", {})
        node = bucket.get(pool_id)
        if not isinstance(node, dict):
            node = {"showCnt": 0, "hitCharState": 0, "hitCharId": ""}
            bucket[pool_id] = node
        node["showCnt"] = max(0, _int(node.get("showCnt"), 0)) + count
        state = _int(node.get("hitCharState"), 0)
        node["hitCharState"] = state if state in (0, 1, 2) else 0
        node["hitCharId"] = str(node.get("hitCharId") or "")
        return gacha
    if rule == "LIMITED" or pool_id.startswith("LIMITED"):
        bucket = gacha.setdefault("limit", {})
        node = bucket.get(pool_id)
        if not isinstance(node, dict):
            node = {"leastFree": 0, "poolCnt": 0, "recruitedFreeChar": False}
            bucket[pool_id] = node
        node["leastFree"] = max(0, _int(node.get("leastFree"), 0))
        node["poolCnt"] = max(0, _int(node.get("poolCnt"), 0)) + count
        node["recruitedFreeChar"] = bool(node.get("recruitedFreeChar", False))
        return gacha
    if rule == "SINGLE":
        if guarantee_node is None:
            return gacha
        guarantee_node.update(
            {
                "singleEnsureCnt": max(0, _int(guarantee_node.get("singleEnsureCnt"), 0)),
                "singleEnsureUse": bool(guarantee_node.get("singleEnsureUse", False)),
                "singleEnsureChar": str(guarantee_node.get("singleEnsureChar") or ""),
            }
        )
        return gacha
    if rule in ("ATTAIN", "CLASSIC_ATTAIN"):
        bucket = gacha.setdefault("attain", {})
        node = bucket.get(pool_id)
        if not isinstance(node, dict):
            node = {"attain6Count": 0}
            bucket[pool_id] = node
        six_stars = sum(1 for row in (results or []) if _int(row.get("rarity"), 0) == 5)
        node["attain6Count"] = max(0, _int(node.get("attain6Count"), 0)) + six_stars
        return gacha
    if rule == "FESCLASSIC":
        bucket = gacha.setdefault("fesClassic", {})
        node = bucket.get(pool_id)
        if not isinstance(node, dict):
            node = {"upChar": {}}
            bucket[pool_id] = node
        if not isinstance(node.get("upChar"), dict):
            node["upChar"] = {}
        return gacha
    if rule == "SPECIAL":
        bucket = gacha.setdefault("special", {})
        node = bucket.get(pool_id)
        if not isinstance(node, dict):
            node = {"upChar": {}}
            bucket[pool_id] = node
        if not isinstance(node.get("upChar"), dict):
            node["upChar"] = {}
        return gacha
    if rule == "BACKFLOW":
        bucket = gacha.setdefault("backflow", {})
        node = bucket.get(pool_id)
        if not isinstance(node, dict):
            node = {"upChar": {}}
            bucket[pool_id] = node
        if not isinstance(node.get("upChar"), dict):
            node["upChar"] = {}
        return gacha
    if rule == "LINKAGE":
        bucket = gacha.setdefault("linkage", {})
        if not isinstance(bucket.get(pool_id), dict):
            bucket[pool_id] = {}
        return gacha
    # NORMAL / CLASSIC / GACHAWITHSPECIFIED6 share the ordinary pool state,
    # which was already written by ``_update_guarantee`` above.
    return gacha


def draw(user, pool_id, count=1, use_tkt=0, item_id="", item_list=None, now=None):
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
    price = _int(meta.get("tenPrice" if count == 10 else "singlePrice"), 600 * count)
    # Work on a detached copy so a bad/missing portion of a combine ticket
    # list cannot consume the first item and then fail the draw half-way.
    target = user
    working = copy.deepcopy(user)
    charge_error = _charge_gacha(
        working,
        pool_id,
        count,
        use_tkt,
        item_id,
        item_list,
        price,
    )
    if charge_error:
        return charge_error
    user = working
    rule = str(meta.get("gachaRuleType") or "")
    double_node = None
    double_base_show = 0
    if rule in ("DOUBLE", "CLASSIC_DOUBLE"):
        double_bucket = user.setdefault("gacha", {}).setdefault("double", {})
        double_node = double_bucket.get(str(pool_id))
        if not isinstance(double_node, dict):
            double_node = {"showCnt": 0, "hitCharState": 0, "hitCharId": ""}
            double_bucket[str(pool_id)] = double_node
        double_node["hitCharState"] = _int(double_node.get("hitCharState"), 0)
        double_node["hitCharId"] = str(double_node.get("hitCharId") or "")
        double_base_show = max(0, _int(double_node.get("showCnt"), 0))
    ex = user.setdefault("exgachadata", {})
    pity_key = _pity_key(pool_id)
    pity = _int(ex.get(pity_key), 0)
    rates, _ = _rarity_rates(detail, pity)
    results = []
    hit_high = False
    # The client shows ``maxCnt - cnt``.  Enforce the same N-th-pull rule for
    # both ten-pulls and ten singles instead of promoting every single pull.
    guarantee_at = _guarantee_force_index(user, pool_id, meta, count)
    detail_has_rate_up = bool(detail.get("hasRateUp", True))
    for index in range(count):
        rarity = _pick_rarity(rates)
        if index == guarantee_at and not hit_high and rarity < 4:
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
        forced_char_id = ""
        if double_node is not None and rarity == 5:
            total = double_base_show + index + 1
            state = _int(double_node.get("hitCharState"), 0)
            chosen = double_node.get("hitCharIds") or {}
            if state == 0 and total >= 150:
                forced_char_id = str(chosen.get("1") or double_node.get("hitCharId") or "")
                if forced_char_id not in chars:
                    forced_char_id = random.choice(up or chars) if (up or chars) else ""
                if forced_char_id:
                    double_node["hitCharState"] = 1
                    double_node["hitCharId"] = forced_char_id
            elif state == 1 and total >= 300:
                forced_char_id = str(chosen.get("2") or double_node.get("hitCharId") or "")
                if forced_char_id not in chars:
                    forced_char_id = random.choice(up or chars) if (up or chars) else ""
                if forced_char_id:
                    double_node["hitCharState"] = 2
                    double_node["hitCharId"] = forced_char_id
        if forced_char_id:
            char_id = forced_char_id
        elif detail_has_rate_up and up and random.random() < up_rate:
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
    gacha = _pool_state(user, pool_id, count, results=results)
    delta = {
        "deleted": {},
        "modified": {
            key: user.get(key)
            for key in ("status", "troop", "inventory", "consumable", "gacha", "dexNav", "building", "exgachadata")
            if key in user
        },
    }
    # Publish the staged mutation only after the whole draw succeeded.
    # ``draw`` callers keep using their original player dictionary, while the
    # route's save_player call sees the completed state.
    target.clear()
    target.update(working)
    response = {"result": 0, "playerDataDelta": delta}
    if count == 1:
        response["charGet"] = results[0]
    else:
        response["gachaResultList"] = results
    return response


def choose_pool_up(user, pool_id, choose_char):
    """Persist the 150/300-pull selector used by DOUBLE pools."""
    pool_id = str(pool_id or "")
    meta = pool_meta(pool_id)
    if not meta or str(meta.get("gachaRuleType") or "") not in ("DOUBLE", "CLASSIC_DOUBLE"):
        return _error(5771, "卡池不存在或不是选调卡池")
    detail = pool_detail(pool_id)
    if not detail:
        return _error(5771, "卡池详情不可用")
    featured, _ = _up_chars(detail, 5)
    available, _ = _detail_chars(detail, 5)
    valid = set(featured or available)
    if not valid:
        return _error(5771, "卡池没有可选六星干员")
    gacha = _ensure_gacha(user)
    bucket = gacha.setdefault("double", {})
    node = bucket.get(pool_id)
    if not isinstance(node, dict):
        node = {"showCnt": 0, "hitCharState": 0, "hitCharId": ""}
        bucket[pool_id] = node
    chosen = node.get("hitCharIds")
    if not isinstance(chosen, dict):
        chosen = {}
    state = _int(node.get("hitCharState"), 0)
    for raw_key, raw_value in (choose_char or {}).items():
        try:
            index = int(raw_key)
        except (TypeError, ValueError):
            continue
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        char_id = str(values[0]) if values else ""
        if not char_id:
            continue
        if char_id not in valid:
            return _error(5771, f"干员 {char_id} 不在本期选调范围")
        if index == 0:
            stage = "1"
        elif index == 1:
            stage = "1" if state == 0 and "1" not in chosen else "2"
        else:
            stage = "2"
        chosen[stage] = char_id
    if not chosen:
        return _error(5771, "没有提交有效的选调干员")
    node["hitCharIds"] = chosen
    if state >= 1 and "2" in chosen:
        node["hitCharId"] = str(chosen["2"])
    else:
        node["hitCharId"] = str(chosen.get("1") or next(iter(chosen.values())))
    node["hitCharState"] = state if state in (0, 1, 2) else 0
    node["showCnt"] = max(0, _int(node.get("showCnt"), 0))
    return {
        "result": 0,
        "playerDataDelta": {"deleted": {}, "modified": {"gacha": gacha}},
    }


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

