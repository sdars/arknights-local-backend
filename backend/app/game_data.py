import json
import threading
from pathlib import Path

from .config import BASE_DIR


ROOT_DIR = BASE_DIR.parent
EXCEL_DIR = ROOT_DIR / "server" / "data" / "excel"
BATTLE_DIR = ROOT_DIR / "server" / "data" / "battle"

_lock = threading.Lock()
_cache = {}


def _load(path):
    path = Path(path)
    mtime = path.stat().st_mtime if path.exists() else None
    key = str(path)
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        _cache[key] = (mtime, data)
        return data


def excel(name):
    return _load(EXCEL_DIR / name)


def mission_table():
    return excel("mission_table.json") or {}


def mission(mission_id):
    return (mission_table().get("missions") or {}).get(str(mission_id or ""))


def mission_group(group_id):
    return (mission_table().get("missionGroups") or {}).get(str(group_id or ""))


def periodical_reward(reward_id):
    table = mission_table()
    for key in ("periodicalRewards", "weeklyRewards"):
        reward = (table.get(key) or {}).get(str(reward_id or ""))
        if reward:
            return reward
    return None


def stage_table():
    return excel("stage_table.json") or {}


def stage(stage_id):
    return (stage_table().get("stages") or {}).get(str(stage_id or ""))


def character(char_id):
    return (excel("character_table.json") or {}).get(str(char_id or ""))


def item_pack(pack_id):
    table = excel("item_table.json") or {}
    return (table.get("itemPackInfos") or {}).get(str(pack_id or ""))


def checkin_group(group_id):
    table = excel("checkin_table.json") or {}
    return (table.get("groups") or {}).get(str(group_id or ""))


def battle_stage_links():
    payload = _load(BATTLE_DIR / "stage.json")
    if not isinstance(payload, dict):
        return {}
    merged = {}
    for group in payload.values():
        if isinstance(group, dict):
            merged.update(group)
    return merged


def next_stages(stage_id):
    links = battle_stage_links()
    entry = links.get(str(stage_id or "")) or {}
    result = []
    for key in ("next", "sub", "star", "hard"):
        target = entry.get(key)
        if target and target not in result:
            result.append(target)
    return result


def first_clear_rewards(stage_id):
    entry = stage(stage_id) or {}
    drop_info = entry.get("stageDropInfo") or {}
    items = []
    wanted = {"ONCE", "COMPLETE"}
    for reward in drop_info.get("displayDetailRewards") or []:
        if reward.get("dropType") not in wanted:
            continue
        if not reward.get("id") or not reward.get("type"):
            continue
        items.append(
            {
                "id": str(reward["id"]),
                "type": str(reward["type"]),
                "count": int(reward.get("count") or 1),
            }
        )
    return items


def normal_stage_rewards(stage_id):
    entry = stage(stage_id) or {}
    drop_info = entry.get("stageDropInfo") or {}
    normal = []
    additional = []
    for reward in drop_info.get("displayDetailRewards") or []:
        if reward.get("occPercent") != "ALWAYS":
            continue
        if not reward.get("id") or not reward.get("type"):
            continue
        item = {
            "id": str(reward["id"]),
            "type": str(reward["type"]),
            "count": int(reward.get("count") or 1),
        }
        if reward.get("dropType") == "NORMAL":
            normal.append(item)
        elif reward.get("dropType") == "ADDITIONAL":
            additional.append(item)
    return normal, additional


def checkin_reward(group_id, index):
    group = checkin_group(group_id) or {}
    items = group.get("items") or []
    if 0 <= index < len(items):
        item = items[index]
        return {
            "id": str(item.get("itemId", "")),
            "type": str(item.get("itemType", "")),
            "count": int(item.get("count") or 1),
        }
    return None
