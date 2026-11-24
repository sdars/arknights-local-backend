"""Friend and social service backed by accounts and player saves."""

import json

from . import game_data
from .db import connect, now_ts
from .services import account_by_uid, save_player


DEFAULT_BOARD = ["RHODES", "GLASGOW", "URSUS", "RHINE", "PENGUIN", "KJERAG"]


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dict(parent, key):
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def _empty_delta():
    return {"modified": {}, "deleted": {}}


def parse_uid(value):
    if value is None:
        return -1
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return -1


def load_friend(user):
    friend = _dict(user, "friend")
    for key in ("list", "request", "star"):
        if not isinstance(friend.get(key), list):
            friend[key] = []
    if not isinstance(friend.get("alias"), dict):
        friend["alias"] = {}
    return friend


def friend_uids(friend):
    return [
        str(parse_uid(entry))
        for entry in friend.get("list") or []
        if parse_uid(entry) > 0
    ]


def is_friend(friend, uid):
    uid = parse_uid(uid)
    return any(parse_uid(entry) == uid for entry in friend.get("list") or [])


def request_direction(friend, uid):
    uid = parse_uid(uid)
    for entry in friend.get("request") or []:
        if isinstance(entry, dict):
            if parse_uid(entry.get("uid")) == uid:
                return _int(entry.get("direction"), 0)
        elif parse_uid(entry) == uid:
            return 0
    return -1


def _append_request(friend, uid, direction):
    requests = friend.setdefault("request", [])
    for index, entry in enumerate(list(requests)):
        if isinstance(entry, dict) and parse_uid(entry.get("uid")) == uid:
            requests[index] = {"uid": str(uid), "direction": direction, "ts": now_ts()}
            return
    requests.append({"uid": str(uid), "direction": direction, "ts": now_ts()})


def _remove_request(friend, uid):
    requests = friend.get("request")
    if not isinstance(requests, list):
        return
    requests[:] = [
        entry
        for entry in requests
        if parse_uid(entry.get("uid") if isinstance(entry, dict) else entry) != parse_uid(uid)
    ]


def _add_friend(friend, uid):
    if not is_friend(friend, uid):
        friend.setdefault("list", []).append(str(uid))


def _remove_friend(friend, uid):
    friend["list"][:] = [
        entry
        for entry in friend.get("list") or []
        if parse_uid(entry) != parse_uid(uid)
    ]


def alias_of(friend, uid):
    aliases = friend.get("alias") or {}
    return str(aliases.get(str(uid)) or "")


def friend_status(friend, uid):
    if is_friend(friend, uid):
        return 2
    if request_direction(friend, uid) == 0:
        return 1
    return 0


def ensure_state(user):
    friend = load_friend(user)
    push = _dict(user, "pushFlags")
    push["hasFriendRequest"] = 1 if any(
        _int(entry.get("direction"), 0) == 1
        for entry in friend.get("request") or []
        if isinstance(entry, dict)
    ) else 0
    return friend


def _target_user(uid):
    account = account_by_uid(uid)
    if not account:
        return None, None
    try:
        return account, json.loads(account["user_json"])
    except (TypeError, ValueError, KeyError):
        return account, None


def _count_furniture(building):
    furniture = (building or {}).get("furniture") or {}
    if not isinstance(furniture, dict):
        return 0
    nested = furniture.get("furnitures")
    if isinstance(nested, dict):
        return len(nested)
    return len(furniture)


def _build_board(chars):
    counts = {}
    for char in chars.values():
        if not isinstance(char, dict):
            continue
        meta = game_data.character(char.get("charId")) or {}
        group = str(meta.get("groupId") or meta.get("nationId") or "").upper()
        if group:
            counts[group] = counts.get(group, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    board = [name for name, _count in ordered[:6]]
    for name in DEFAULT_BOARD:
        if len(board) >= 6:
            break
        if name not in board:
            board.append(name)
    return board


def _build_team_v2(chars):
    team = {}
    for char in chars.values():
        if not isinstance(char, dict):
            continue
        meta = game_data.character(char.get("charId")) or {}
        team_id = str(meta.get("teamId") or "")
        if team_id:
            team[team_id] = team.get(team_id, 0) + 1
    return team


def _shared_char(char, slot):
    skills = []
    for skill in char.get("skills") or []:
        if isinstance(skill, dict) and skill.get("skillId"):
            skills.append({
                "skillId": str(skill.get("skillId")),
                "specializeLevel": _int(skill.get("specializeLevel"), 0),
            })
    char_id = str(char.get("charId") or "")
    skill_index = slot.get("skillIndex")
    if skill_index is None:
        skill_index = char.get("defaultSkillIndex", 0)
    return {
        "charId": char_id,
        "skinId": str(char.get("skin") or (char_id + "#1")),
        "skills": skills,
        "mainSkillLvl": _int(char.get("mainSkillLvl"), 1),
        "skillIndex": max(0, _int(skill_index, 0)),
        "evolvePhase": _int(char.get("evolvePhase"), 0),
        "favorPoint": _int(char.get("favorPoint"), 0),
        "potentialRank": _int(char.get("potentialRank"), 0),
        "level": _int(char.get("level"), 1),
        "crisisRecord": {},
        "crisisV2Record": {},
        "currentEquip": slot.get("currentEquip") or None,
        "equip": char.get("equip") if isinstance(char.get("equip"), dict) else {},
    }


def _assist_list(user):
    social = user.get("social") or {}
    stored = social.get("assistCharList")
    chars = (user.get("troop") or {}).get("chars") or {}
    if not isinstance(stored, list) or not stored:
        return [None]
    result = []
    for slot in stored:
        if not isinstance(slot, dict):
            result.append(None)
            continue
        char = chars.get(str(slot.get("charInstId") or ""))
        if not isinstance(char, dict):
            result.append(None)
            continue
        result.append(_shared_char(char, slot))
    return result


def _name_card_style(user, skin):
    style = user.get("nameCardStyle")
    if isinstance(style, dict):
        component = style.get("componentOrder")
        if not isinstance(component, list) or not component:
            component = ["module_sign", "module_assist", "module_medal"]
        misc = style.get("misc")
        if not isinstance(misc, dict):
            misc = {"showDetail": True, "showBirthday": False}
        return {"componentOrder": component, "skin": skin, "misc": misc}
    return {
        "componentOrder": ["module_sign", "module_assist", "module_medal"],
        "skin": skin,
        "misc": {"showDetail": True, "showBirthday": False},
    }


def build_detail(target, uid, recent_visited=False):
    status = target.get("status") or {}
    troop = target.get("troop") or {}
    chars = troop.get("chars") or {}
    social = target.get("social") or {}
    building = target.get("building") or {}
    style = target.get("nameCardStyle") if isinstance(target.get("nameCardStyle"), dict) else {}
    skin = style.get("skin") if isinstance(style.get("skin"), dict) else {}
    selected = str(skin.get("selected") or "nc_rhodes_default")
    card_skin = {
        "selected": selected,
        "state": skin.get("state") if isinstance(skin.get("state"), dict) else {},
        "tmpl": skin.get("tmpl") if isinstance(skin.get("tmpl"), dict) else {},
    }
    medal = social.get("medalBoard")
    if not isinstance(medal, dict):
        medal = {"type": "EMPTY", "custom": None, "template": None}
    avatar = status.get("avatar")
    if not isinstance(avatar, dict):
        avatar = {}
    birthday = status.get("birthday")
    if not isinstance(birthday, dict):
        birthday = {}
    month = _int(birthday.get("month"), 0)
    day = _int(birthday.get("day"), 0)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        month, day = 1, 1
    skin_count = len(((target.get("skin") or {}).get("characterSkins") or {}))
    return {
        "nickName": status.get("nickName"),
        "nickNumber": status.get("nickNumber"),
        "uid": str(uid),
        "registerTs": _int(status.get("registerTs"), 0),
        "mainStageProgress": status.get("mainStageProgress"),
        "charCnt": len(chars),
        "furnCnt": _count_furniture(building),
        "skinCnt": skin_count,
        "secretary": status.get("secretary"),
        "secretarySkinId": status.get("secretarySkinId"),
        "secretarySkinSp": bool(status.get("secretarySkinSp")),
        "resume": status.get("resume") or "",
        "teamV2": _build_team_v2(chars),
        "friendNumLimit": _int(status.get("friendNumLimit"), 0),
        "serverName": status.get("serverName"),
        "level": _int(status.get("level"), 1),
        "avatarId": status.get("avatarId"),
        "avatar": avatar,
        "assistCharList": _assist_list(target),
        "lastOnlineTime": _int(status.get("lastOnlineTs"), 0),
        "board": _build_board(chars),
        "infoShare": 0,
        "medalBoard": medal,
        "birthday": {"month": month, "day": day},
        "equipStatus": {
            "unlockCnt": 0,
            "limitedEquipCnt": 0,
            "level3EquipCnt": 0,
            "equipCharCnt": 0,
            "limitedCharCnt": 0,
        },
        "nameCardStyle": _name_card_style(target, card_skin),
        "activity": {},
        "magazineBoard": [],
        "friendPrivate": 0,
        "recentVisited": bool(recent_visited),
        "clueSendToMe": 0,
        "clueReceiveFromMe": [],
        "skin": card_skin,
    }


def build_view(target, uid, recent_visited=False):
    status = target.get("status") or {}
    return {
        "uid": str(uid),
        "level": _int(status.get("level"), 1),
        "infoShare": 0,
        "infoShareVisited": 0,
        "recentVisited": bool(recent_visited),
        "lastOnlineTime": _int(status.get("lastOnlineTs"), 0),
        "activity": {},
    }


def _result(code):
    return {"result": code, "playerDataDelta": _empty_delta(), "pushMessage": []}


def _search_accounts(query):
    query = str(query or "").strip()
    if not query:
        return []
    with connect() as conn:
        rows = conn.execute(
            "SELECT uid, user_json FROM accounts WHERE nickname LIKE ? OR CAST(uid AS TEXT)=? ORDER BY uid LIMIT 20",
            ("%" + query + "%", query),
        ).fetchall()
    result = []
    for row in rows:
        try:
            result.append((int(row["uid"]), json.loads(row["user_json"])))
        except (TypeError, ValueError, KeyError):
            continue
    return result


def search_players(account, user, body):
    friend = load_friend(user)
    players = []
    statuses = []
    ids = []
    for raw in body.get("idList") or []:
        uid = parse_uid(raw)
        if uid <= 0:
            continue
        _target_account, target = _target_user(uid)
        if not target:
            continue
        players.append(build_detail(target, uid, False))
        statuses.append(friend_status(friend, uid))
        ids.append(str(uid))
    return {
        "players": players,
        "friendStatusList": statuses,
        "resultIdList": ids,
        "playerDataDelta": _empty_delta(),
    }


def sort_list(account, user, body):
    friend = load_friend(user)
    my_uid = _int(account.get("uid"), 0)
    service_type = _int(body.get("type"), 1)
    param = body.get("param") if isinstance(body.get("param"), dict) else {}
    result = []
    stars = []
    if service_type == 0:
        for uid, target in _search_accounts(param.get("nickName")):
            if uid != my_uid:
                result.append(build_view(target, uid, False))
    elif service_type == 1:
        for uid in friend_uids(friend):
            _target_account, target = _target_user(uid)
            if target:
                result.append(build_view(target, uid, True))
        stars = [str(uid) for uid in friend.get("star") or [] if parse_uid(uid) > 0]
    elif service_type == 2:
        for entry in friend.get("request") or []:
            if isinstance(entry, dict) and _int(entry.get("direction"), 0) == 1:
                uid = parse_uid(entry.get("uid"))
                if uid > 0:
                    result.append({"uid": str(uid)})
    return {"result": result, "starFriendList": stars, "playerDataDelta": _empty_delta()}


def send_friend_request(account, user, body):
    my_uid = _int(account.get("uid"), 0)
    target_uid = parse_uid(body.get("friendId"))
    if target_uid <= 0 or target_uid == my_uid:
        return _result(3)
    target_account, target = _target_user(target_uid)
    if not target:
        return _result(3)
    friend = load_friend(user)
    if is_friend(friend, target_uid):
        return _result(1)
    if request_direction(friend, target_uid) == 0:
        return _result(2)
    target_friend = load_friend(target)
    if request_direction(target_friend, my_uid) == 1:
        return _result(0)
    _append_request(friend, target_uid, 0)
    _append_request(target_friend, my_uid, 1)
    save_player(account, user)
    save_player(target_account, target)
    return _result(0)


def get_friend_list(account, user, body):
    friend = load_friend(user)
    ids = body.get("idList")
    if not isinstance(ids, list):
        ids = friend_uids(friend)
    friends = []
    aliases = []
    result_ids = []
    for raw in ids:
        uid = parse_uid(raw)
        if uid <= 0:
            continue
        _target_account, target = _target_user(uid)
        if not target:
            continue
        friends.append(build_detail(target, uid, True))
        aliases.append(alias_of(friend, uid))
        result_ids.append(str(uid))
    return {
        "friends": friends,
        "friendAlias": aliases,
        "resultIdList": result_ids,
        "playerDataDelta": _empty_delta(),
        "pushMessage": [],
    }


def get_friend_request_list(account, user, body):
    ids = body.get("idList") if isinstance(body.get("idList"), list) else []
    request_list = []
    result_ids = []
    for raw in ids:
        uid = parse_uid(raw)
        if uid <= 0:
            continue
        _target_account, target = _target_user(uid)
        if not target:
            continue
        request_list.append(build_detail(target, uid, False))
        result_ids.append(str(uid))
    return {
        "requestList": request_list,
        "resultIdList": result_ids,
        "playerDataDelta": _empty_delta(),
        "pushMessage": [],
    }


def friend_and_send_list(account, user):
    friend = load_friend(user)
    friends = [
        {"uid": uid, "friendAlias": alias_of(friend, uid) or None}
        for uid in friend_uids(friend)
    ]
    sent = [
        str(parse_uid(entry.get("uid")))
        for entry in friend.get("request") or []
        if isinstance(entry, dict)
        and _int(entry.get("direction"), 0) == 0
        and parse_uid(entry.get("uid")) > 0
    ]
    return {"friendsList": friends, "requestSendIdList": sent}


def set_assist_chars(account, user, body):
    incoming = body.get("assistCharList")
    if not isinstance(incoming, list):
        return {"result": 1, "playerDataDelta": _empty_delta()}
    chars = (user.get("troop") or {}).get("chars") or {}
    stored = []
    for raw in incoming:
        if raw is None:
            stored.append(None)
            continue
        if not isinstance(raw, dict):
            return {"result": 1, "playerDataDelta": _empty_delta()}
        inst_id = _int(raw.get("charInstId"), 0)
        char = chars.get(str(inst_id))
        if not isinstance(char, dict):
            return {"result": 1, "playerDataDelta": _empty_delta()}
        slot = {
            "charInstId": inst_id,
            "skillIndex": max(0, _int(raw.get("skillIndex"), _int(char.get("defaultSkillIndex"), 0))),
            "currentEquip": raw.get("currentEquip") or None,
        }
        if raw.get("currentTmpl"):
            slot["currentTmpl"] = raw.get("currentTmpl")
        if isinstance(raw.get("tmpl"), dict):
            slot["tmpl"] = raw.get("tmpl")
        stored.append(slot)
    social = _dict(user, "social")
    social["assistCharList"] = stored
    save_player(account, user)
    return {
        "result": 0,
        "playerDataDelta": {"modified": {"social": {"assistCharList": stored}}, "deleted": {}},
    }


def process_request(account, user, body):
    my_uid = _int(account.get("uid"), 0)
    target_uid = parse_uid(body.get("friendId"))
    action = _int(body.get("action"), 0)
    friend = load_friend(user)
    if target_uid <= 0 or target_uid == my_uid or request_direction(friend, target_uid) != 1:
        return _process_result(1, friend, user)
    target_account, target = _target_user(target_uid)
    if not target:
        return _process_result(1, friend, user)
    target_friend = load_friend(target)
    _remove_request(friend, target_uid)
    _remove_request(target_friend, my_uid)
    if action == 1:
        _add_friend(friend, target_uid)
        _add_friend(target_friend, my_uid)
    result = _process_result(0, friend, user)
    save_player(account, user)
    save_player(target_account, target)
    return result


def _process_result(code, friend, user):
    incoming = any(
        isinstance(entry, dict) and _int(entry.get("direction"), 0) == 1
        for entry in friend.get("request") or []
    )
    push = _dict(user, "pushFlags")
    push["hasFriendRequest"] = 1 if incoming else 0
    return {
        "result": code,
        "friendNum": len(friend_uids(friend)),
        "playerDataDelta": {
            "modified": {"pushFlags": {"hasFriendRequest": push["hasFriendRequest"]}},
            "deleted": {},
        },
        "pushMessage": [],
    }


def delete_friend(account, user, body):
    my_uid = _int(account.get("uid"), 0)
    target_uid = parse_uid(body.get("friendId"))
    friend = load_friend(user)
    if target_uid <= 0 or not is_friend(friend, target_uid):
        return _result(1)
    _remove_friend(friend, target_uid)
    friend["star"] = [
        entry for entry in friend.get("star") or [] if parse_uid(entry) != target_uid
    ]
    (friend.get("alias") or {}).pop(str(target_uid), None)
    save_player(account, user)
    target_account, target = _target_user(target_uid)
    if target:
        target_friend = load_friend(target)
        _remove_friend(target_friend, my_uid)
        save_player(target_account, target)
    return _result(0)


def set_star_list(account, user, body):
    friend = load_friend(user)
    new_ids = []
    for raw in body.get("idList") or []:
        uid = parse_uid(raw)
        if uid > 0:
            new_ids.append(str(uid))
    friend["star"] = new_ids
    modified = {}
    if not new_ids:
        social = _dict(user, "social")
        social["starFriendFlag"] = 1
        modified["social"] = {"starFriendFlag": 1}
    save_player(account, user)
    return {
        "result": 0,
        "newIdList": new_ids,
        "playerDataDelta": {"modified": modified, "deleted": {}},
    }


def set_alias(account, user, body):
    friend = load_friend(user)
    target_uid = parse_uid(body.get("friendId"))
    if target_uid <= 0 or not is_friend(friend, target_uid):
        return _result(6)
    aliases = _dict(friend, "alias")
    alias = str(body.get("alias") or "")
    if alias:
        aliases[str(target_uid)] = alias
    else:
        aliases.pop(str(target_uid), None)
    save_player(account, user)
    return _result(0)


def name_card(account, user, body):
    target_uid = parse_uid(body.get("uid"))
    if target_uid <= 0:
        return {"result": 1, "playerDataDelta": _empty_delta()}
    _target_account, target = _target_user(target_uid)
    if not target:
        return {"result": 1, "playerDataDelta": _empty_delta()}
    friend = load_friend(user)
    return {
        "friendStatus": friend_status(friend, target_uid),
        "nameCard": build_detail(target, target_uid, False),
        "playerDataDelta": _empty_delta(),
        "pushMessage": [],
    }
