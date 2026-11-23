import json
import re
import time

from . import game_data


STATUS_CURRENCIES = {
    "DIAMOND_SHD": "diamondShard",
    "TKT_GACHA": "gachaTicket",
    "TKT_GACHA_10": "tenGachaTicket",
    "CLASSIC_TKT_GACHA": "classicGachaTicket",
    "CLASSIC_TKT_GACHA_10": "classicTenGachaTicket",
    "TKT_RECRUIT": "recruitLicense",
    "TKT_INST_FIN": "instantFinishTicket",
    "TKT_TRY": "practiceTicket",
    "HGG_SHD": "hggShard",
    "LGG_SHD": "lggShard",
    "SOCIAL_PT": "socialPoint",
    "AP_GAMEPLAY": "ap",
    "CLASSIC_SHARD": "classicShard",
}

INVENTORY_TYPES = {
    "MATERIAL",
    "CARD_EXP",
    "AP_SUPPLY",
    "TKT_GACHA_PRSV",
    "RETRO_COIN",
    "ACTIVITY_ITEM",
    "AUTOCHESS_SKIN",
    "STICKER",
}


def normalize_platform(value):
    text = str(value or "").strip().lower()
    if text in {"2", "ios", "iphone", "ipad", "appstore"}:
        return "ios"
    if text in {"1", "android", "googleplay", "google"}:
        return "android"
    return ""


def diamond_status_key(user, platform=None):
    resolved = normalize_platform(platform) or normalize_platform((user or {}).get("localPlatform"))
    return "iosDiamond" if resolved == "ios" else "androidDiamond"


def _dict(parent, key):
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def _list(parent, key):
    value = parent.get(key)
    if not isinstance(value, list):
        value = []
        parent[key] = value
    return value


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _add(mapping, key, amount):
    mapping[key] = _int(mapping.get(key)) + amount
    return mapping[key]


def _normalize(item):
    if not isinstance(item, dict):
        return None
    item_id = str(item.get("id") or item.get("itemId") or "")
    item_type = str(item.get("type") or item.get("itemType") or "").upper()
    count = _int(item.get("count"), 1)
    if not item_id or not item_type or count <= 0:
        return None
    return {"id": item_id, "type": item_type, "count": count}


def _coalesce(items):
    order = []
    merged = {}
    for raw in items or []:
        item = _normalize(raw)
        if not item:
            continue
        key = (item["type"], item["id"])
        if key not in merged:
            merged[key] = dict(item)
            order.append(key)
        else:
            merged[key]["count"] += item["count"]
    return [merged[key] for key in order]


def _debt_map(user):
    debt = user.get("revokeDebt")
    if not isinstance(debt, dict):
        debt = {}
        user["revokeDebt"] = debt
    return debt


def _debtable(item_type):
    return (
        item_type in {"GOLD", "DIAMOND", "FURN", "MCARD_VOUCHER", "RENAMING_CARD"}
        or item_type in STATUS_CURRENCIES
        or item_type in INVENTORY_TYPES
        or "VOUCHER" in item_type
    )


def _debt_key(user, item, platform=None):
    if not _debtable(item["type"]):
        return ""
    if item["type"] == "DIAMOND":
        return f"DIAMOND:{diamond_status_key(user, platform)}:{item['id']}"
    return f"{item['type']}:{item['id']}"


def _offset_debt(user, item, platform=None):
    key = _debt_key(user, item, platform=platform)
    if not key:
        return item
    debt = _debt_map(user)
    amount = _int(debt.get(key), 0)
    if amount <= 0:
        return item
    used = min(amount, item["count"])
    remaining = amount - used
    if remaining:
        debt[key] = remaining
    else:
        debt.pop(key, None)
    if used >= item["count"]:
        return None
    result = dict(item)
    result["count"] = item["count"] - used
    return result


def clear_debt(user, items, platform=None):
    debt = _debt_map(user)
    for item in _coalesce(items):
        key = _debt_key(user, item, platform=platform)
        if not key:
            continue
        remaining = max(0, _int(debt.get(key), 0) - item["count"])
        if remaining:
            debt[key] = remaining
        else:
            debt.pop(key, None)
    return debt


def clear_debt_map(user, mapping):
    debt = _debt_map(user)
    for key, value in (mapping or {}).items():
        remaining = max(0, _int(debt.get(key), 0) - _int(value, 0))
        if remaining:
            debt[key] = remaining
        else:
            debt.pop(key, None)
    return debt


def debt_rows(user):
    debt = _debt_map(user)
    return [{"key": key, "count": value} for key, value in sorted(debt.items()) if value]


def _ensure_skin(user):
    skin = _dict(user, "skin")
    _dict(skin, "skinTs")
    _dict(skin, "characterSkins")
    return skin


def _ensure_avatar(user):
    avatar = _dict(user, "avatar")
    _dict(avatar, "avatar_icon")
    return avatar


def _ensure_consumable(user, item_id):
    consumable = _dict(user, "consumable")
    node = consumable.get(item_id)
    if not isinstance(node, dict):
        node = {}
        consumable[item_id] = node
    slot = node.get("0")
    if not isinstance(slot, dict):
        slot = {"ts": -1, "count": 0}
        node["0"] = slot
    return slot


def _default_skin(char_id):
    return f"{char_id}#1"


def _char_inst_id(user, char_id):
    chars = _dict(_dict(user, "troop"), "chars")
    used = set()
    for key, value in chars.items():
        if isinstance(value, dict):
            used.add(_int(value.get("instId"), _int(key, 0)))
        else:
            used.add(_int(key, 0))
    match = re.search(r"char_(\d+)_", char_id)
    preferred = _int(match.group(1)) if match else 0
    if preferred > 0 and preferred not in used:
        return preferred
    return max(used or {0}) + 1


def _character_data(char_id, inst_id, now):
    meta = game_data.character(char_id) or {}
    skills = []
    for skill in meta.get("skills") or []:
        if not isinstance(skill, dict) or not skill.get("skillId"):
            continue
        phase = (skill.get("unlockCond") or {}).get("phase")
        skills.append(
            {
                "skillId": str(skill["skillId"]),
                "unlock": 1 if phase == "PHASE_0" else 0,
                "state": 0,
                "specializeLevel": 0,
                "completeUpgradeTime": -1,
            }
        )
    return {
        "instId": inst_id,
        "charId": char_id,
        "favorPoint": 0,
        "potentialRank": 0,
        "mainSkillLvl": 1,
        "skin": _default_skin(char_id),
        "level": 1,
        "exp": 0,
        "evolvePhase": 0,
        "defaultSkillIndex": 0 if skills else -1,
        "gainTime": now,
        "skills": skills,
        "voiceLan": "CN_MANDARIN",
        "starMark": 0,
        "currentEquip": None,
        "equip": {},
    }


def _building_character(char_id, now):
    return {
        "charId": char_id,
        "lastApAddTime": now,
        "ap": 8640000,
        "roomSlotId": "",
        "index": -1,
        "changeScale": 0,
        "bubble": {
            "normal": {"add": -1, "ts": 0},
            "assist": {"add": -1, "ts": 0},
            "private": {"add": -1, "ts": 0},
        },
        "workTime": 0,
        "privateRooms": [],
    }


def _grant_char(user, char_id, count, now, touched):
    troop = _dict(user, "troop")
    chars = _dict(troop, "chars")
    existing = next(
        (value for value in chars.values() if isinstance(value, dict) and value.get("charId") == char_id),
        None,
    )
    if existing:
        inventory = _dict(user, "inventory")
        _add(inventory, f"p_{char_id}", count)
        touched.update(("troop", "inventory"))
        return

    inventory = _dict(user, "inventory")
    for index in range(count):
        if index:
            _add(inventory, f"p_{char_id}", 1)
            continue
        inst_id = _char_inst_id(user, char_id)
        data = _character_data(char_id, inst_id, now)
        chars[str(inst_id)] = data
        char_group = _dict(troop, "charGroup")
        char_group[char_id] = {"favorPoint": 0}
        building = _dict(user, "building")
        building_chars = _dict(building, "chars")
        building_chars[str(inst_id)] = _building_character(char_id, now)
        inventory.setdefault(f"p_{char_id}", 0)
    touched.update(("troop", "building", "inventory"))


def _grant_monthly_card(user, count, now, touched):
    status = _dict(user, "status")
    start = _int(status.get("monthlySubscriptionStartTime"))
    end = _int(status.get("monthlySubscriptionEndTime"))
    if start <= 0:
        start = now
    end = max(end, now) + 30 * 86400 * max(1, count)
    status["monthlySubscriptionStartTime"] = start
    status["monthlySubscriptionEndTime"] = end
    status["tipMonthlyCardExpireTs"] = end
    touched.add("status")


def _grant_item(user, item, source, now, touched, platform=None):
    item_id = item["id"]
    item_type = item["type"]
    count = item["count"]

    if item_type == "ITEM_PACK":
        pack = game_data.item_pack(item_id) or {}
        for _ in range(count):
            for child in pack.get("content") or []:
                child_item = _normalize(child)
                if child_item:
                    _grant_item(user, child_item, source, now, touched, platform=platform)
        return

    item = _offset_debt(user, item, platform=platform)
    if not item:
        return
    item_id = item["id"]
    item_type = item["type"]
    count = item["count"]

    if item_type == "OPEN_SERVER_CREDIT":
        open_server = _dict(user, "openServerV3")
        milestone = _dict(_dict(open_server, "mission"), "milestone")
        milestone.setdefault("groupId", "new_bp_g_1")
        milestone.setdefault("got", [])
        _add(milestone, "point", count)
        touched.add("openServerV3")
        return

    if item_type == "CHAR":
        _grant_char(user, item_id, count, now, touched)
        return

    if item_type == "CHAR_SKIN":
        skin = _ensure_skin(user)
        skin["characterSkins"][item_id] = 1
        skin["skinTs"][item_id] = now
        touched.add("skin")
        return

    if item_type == "PLAYER_AVATAR":
        avatar = _ensure_avatar(user)
        slot = avatar["avatar_icon"].get(item_id)
        if not isinstance(slot, dict):
            avatar["avatar_icon"][item_id] = {"ts": now, "src": "initial"}
        else:
            slot.setdefault("ts", now)
            slot.setdefault("src", "initial")
        touched.add("avatar")
        return

    if item_type == "GOLD":
        status = _dict(user, "status")
        _add(status, "gold", count)
        touched.add("status")
        return

    if item_type == "DIAMOND":
        status = _dict(user, "status")
        _add(status, diamond_status_key(user, platform), count)
        touched.add("status")
        return

    if item_type in STATUS_CURRENCIES:
        status = _dict(user, "status")
        _add(status, STATUS_CURRENCIES[item_type], count)
        touched.add("status")
        return

    if item_type in INVENTORY_TYPES:
        inventory = _dict(user, "inventory")
        _add(inventory, item_id, count)
        touched.add("inventory")
        return

    if item_type == "EMOTICON_SET":
        emoticon = _dict(user, "emoticon")
        themes = _list(emoticon, "unlockTheme")
        if item_id not in themes:
            themes.append(item_id)
        inventory = _dict(user, "inventory")
        _add(inventory, item_id, count)
        touched.update(("emoticon", "inventory"))
        return

    if item_type == "HOME_THEME":
        home_theme = _dict(user, "homeTheme")
        _dict(home_theme, "themes")[item_id] = {"unlock": now}
        touched.add("homeTheme")
        return

    if item_type == "HOME_BACKGROUND":
        background = _dict(user, "background")
        _dict(background, "bgs")[item_id] = {"unlock": now}
        touched.add("background")
        return

    if item_type == "FURN":
        building = _dict(user, "building")
        furniture = _dict(building, "furniture")
        slot = furniture.get(item_id)
        if not isinstance(slot, dict):
            furniture[item_id] = {"count": count, "inUse": 0}
        else:
            _add(slot, "count", count)
            slot.setdefault("inUse", 0)
        touched.add("building")
        return

    if item_type == "MCARD_VOUCHER":
        _grant_monthly_card(user, count, now, touched)

    inventory = _dict(user, "inventory")
    _add(inventory, item_id, count)
    touched.add("inventory")
    if "VOUCHER" in item_type or item_type in {"RENAMING_CARD", "MCARD_VOUCHER"}:
        slot = _ensure_consumable(user, item_id)
        _add(slot, "count", count)
        touched.add("consumable")


def _take_status(user, key, amount):
    status = _dict(user, "status")
    have = _int(status.get(key), 0)
    take = min(max(0, have), amount)
    status[key] = have - take
    return take, amount - take


def _take_inventory(user, item_id, amount):
    inventory = _dict(user, "inventory")
    have = _int(inventory.get(item_id), 0)
    take = min(max(0, have), amount)
    if take:
        if take >= have:
            inventory.pop(item_id, None)
        else:
            inventory[item_id] = have - take
    return take, amount - take


def _take_consumable(user, item_id, amount):
    consumable = _dict(user, "consumable")
    node = consumable.get(item_id)
    if not isinstance(node, dict):
        return 0, amount
    slot = node.get("0")
    if not isinstance(slot, dict):
        return 0, amount
    have = _int(slot.get("count"), 0)
    take = min(max(0, have), amount)
    slot["count"] = have - take
    return take, amount - take


def _take_char(user, char_id, amount):
    troop = _dict(user, "troop")
    chars = _dict(troop, "chars")
    building = _dict(user, "building")
    building_chars = _dict(building, "chars")
    removed = 0
    for inst_id in list(chars):
        value = chars.get(inst_id)
        if not isinstance(value, dict) or value.get("charId") != char_id:
            continue
        chars.pop(inst_id, None)
        building_chars.pop(str(inst_id), None)
        for squad in (_dict(troop, "squads")).values():
            if not isinstance(squad, dict):
                continue
            for index, slot in enumerate(squad.get("slots") or []):
                if isinstance(slot, dict) and str(slot.get("charInstId") or "") == str(inst_id):
                    squad["slots"][index] = None
        removed += 1
        if removed >= amount:
            break
    if not any(isinstance(value, dict) and value.get("charId") == char_id for value in chars.values()):
        _dict(troop, "charGroup").pop(char_id, None)
    inventory_take = 0
    if removed < amount:
        inventory_take, _ = _take_inventory(user, f"p_{char_id}", amount - removed)
    return removed + inventory_take, amount - removed - inventory_take


def _take_item(user, item, now, platform, touched):
    item_id = item["id"]
    item_type = item["type"]
    count = item["count"]

    if item_type == "ITEM_PACK":
        pack = game_data.item_pack(item_id) or {}
        actual = 0
        short = 0
        for _ in range(count):
            for child in pack.get("content") or []:
                child_item = _normalize(child)
                if not child_item:
                    continue
                child_actual, child_short = _take_item(
                    user, child_item, now, platform, touched
                )
                actual += child_actual
                short += child_short
        return actual, short

    if item_type == "CHAR":
        take, short = _take_char(user, item_id, count)
        touched.update(("troop", "building", "inventory"))
        return take, short

    if item_type == "CHAR_SKIN":
        skin = _ensure_skin(user)
        present = item_id in skin["characterSkins"]
        skin["characterSkins"].pop(item_id, None)
        skin["skinTs"].pop(item_id, None)
        touched.add("skin")
        return (1 if present else 0), (0 if present else count)

    if item_type == "PLAYER_AVATAR":
        avatar = _ensure_avatar(user)
        present = item_id in avatar["avatar_icon"]
        avatar["avatar_icon"].pop(item_id, None)
        touched.add("avatar")
        return (1 if present else 0), (0 if present else count)

    if item_type == "HOME_THEME":
        themes = _dict(_dict(user, "homeTheme"), "themes")
        present = item_id in themes
        themes.pop(item_id, None)
        touched.add("homeTheme")
        return (1 if present else 0), (0 if present else count)

    if item_type == "HOME_BACKGROUND":
        bgs = _dict(_dict(user, "background"), "bgs")
        present = item_id in bgs
        bgs.pop(item_id, None)
        touched.add("background")
        return (1 if present else 0), (0 if present else count)

    if item_type == "FURN":
        furniture = _dict(_dict(user, "building"), "furniture")
        slot = furniture.get(item_id)
        have = _int(slot.get("count"), 0) if isinstance(slot, dict) else 0
        take = min(max(0, have), count)
        if isinstance(slot, dict):
            if take >= have:
                furniture.pop(item_id, None)
            else:
                slot["count"] = have - take
        touched.add("building")
        return take, count - take

    if item_type == "EMOTICON_SET":
        emoticon = _dict(user, "emoticon")
        themes = _list(emoticon, "unlockTheme")
        present = item_id in themes
        if present:
            themes.remove(item_id)
        inventory_take, _ = _take_inventory(user, item_id, 1 if present else count)
        touched.update(("emoticon", "inventory"))
        take = 1 if present else inventory_take
        return take, count - take

    if item_type == "MCARD_VOUCHER":
        take, short = _take_inventory(user, item_id, count)
        if short:
            consumed, short = _take_consumable(user, item_id, short)
            take += consumed
        if take:
            status = _dict(user, "status")
            end = _int(status.get("monthlySubscriptionEndTime"), 0)
            if end:
                new_end = max(now, end - take * 30 * 86400)
                status["monthlySubscriptionEndTime"] = new_end
                status["tipMonthlyCardExpireTs"] = new_end
                if new_end <= now:
                    status["monthlySubscriptionStartTime"] = 0
                    status["monthlySubscriptionEndTime"] = 0
            touched.add("status")
        touched.update(("inventory", "consumable"))
        return take, short

    if item_type == "GOLD":
        take, short = _take_status(user, "gold", count)
        touched.add("status")
        return take, short

    if item_type == "DIAMOND":
        take, short = _take_status(user, diamond_status_key(user, platform), count)
        touched.add("status")
        return take, short

    if item_type in STATUS_CURRENCIES:
        take, short = _take_status(user, STATUS_CURRENCIES[item_type], count)
        touched.add("status")
        return take, short

    take, short = _take_inventory(user, item_id, count)
    if short:
        consumed, short = _take_consumable(user, item_id, short)
        take += consumed
        touched.add("consumable")
    touched.add("inventory")
    return take, short


def revoke_items(user, items, source="revoke", now=None, platform=None):
    now = int(now or time.time())
    normalized = _coalesce(items)
    touched = set()
    revoked = []
    debt = {}
    for item in normalized:
        actual, short = _take_item(user, item, now, platform, touched)
        if actual:
            revoked.append({"id": item["id"], "type": item["type"], "count": actual})
        if short:
            key = _debt_key(user, item, platform=platform)
            if key:
                debt[key] = _int(debt.get(key), 0) + short
    if debt:
        current = _debt_map(user)
        for key, value in debt.items():
            current[key] = _int(current.get(key), 0) + value
    modified = {key: user.get(key) for key in sorted(touched) if key in user}
    return revoked, debt, {"deleted": {}, "modified": modified}


def apply_items(user, items, source="unknown", now=None, platform=None):
    now = int(now or time.time())
    normalized = _coalesce(items)
    touched = set()
    for item in normalized:
        _grant_item(user, item, source, now, touched, platform=platform)
    modified = {key: user.get(key) for key in sorted(touched) if key in user}
    return normalized, {"deleted": {}, "modified": modified}


def merge_delta(target, source):
    if not source:
        return target
    target.setdefault("deleted", {}).update(source.get("deleted") or {})
    target.setdefault("modified", {}).update(source.get("modified") or {})
    return target


def empty_delta():
    return {"deleted": {}, "modified": {}}


def clone(value):
    return json.loads(json.dumps(value, ensure_ascii=False))
