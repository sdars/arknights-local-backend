import copy
import json
import logging
import random
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import BATTLE_START_TEMPLATE
from ..db import now_ts
from ..services import account_by_token, player_data, save_player, touch_login
from ..shop_fixtures import shop_response, skin_good_list as load_skin_goods
from .. import (
    battles,
    checkin as checkin_service,
    gacha_service,
    game_data,
    grants,
    mail_service,
    missions,
    monthly_card,
    payments,
    runtime_settings,
    shop_service,
    social as social_service,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _guide_snapshot(user):
    return copy.deepcopy(
        ((user.get("mission") or {}).get("missions") or {}).get("GUIDE_V3") or {}
    )


def _guide_changed(user, before):
    """Re-derive GUIDE_V3 after a save change and report if it moved."""
    missions.ensure_mission_state(user)
    after = ((user.get("mission") or {}).get("missions") or {}).get("GUIDE_V3") or {}
    return before != after


def empty_delta():
    return {"playerDataDelta": {"deleted": {}, "modified": {}}}


def checkin_delta(checkin):
    return {
        key: checkin.get(key)
        for key in (
            "canCheckIn",
            "checkInHistory",
            "showCount",
            "checkInRewardIndex",
            "lastCheckInTs",
        )
        if key in checkin
    }


def auth(request: Request):
    token = request.headers.get("secret") or request.headers.get("token") or ""
    return account_by_token(token)


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@router.post("/account/login")
async def account_login(request: Request):
    body = await request.json()
    token = str(body.get("token", ""))
    account = account_by_token(token)
    if not account:
        return {"result": 2, "error": "invalid token"}
    if account["banned"]:
        return {"result": 1, "error": "account banned"}
    settings = runtime_settings.values()
    if settings["maintenance"]:
        return {"result": 3, "error": settings["maintenance_message"]}
    client_version = str(body.get("clientVersion", ""))
    assets_version = str(body.get("assetsVersion", ""))
    if client_version and client_version != settings["client_version"] and settings["force_update"]:
        return {"result": 2, "error": "客户端版本需要更新"}
    if assets_version and assets_version != settings["res_version"] and settings["force_resource_update"]:
        return {"result": 4, "error": "资源需要更新"}
    touch_login(
        account,
        body.get("deviceId"),
        request.client.host if request.client else None,
        body.get("platform"),
    )
    data = {
        "result": 0,
        "uid": account["uid"],
        "secret": account["token"],
        "token": account["token"],
        "serviceLicenseVersion": 0,
    }
    return {**data, "data": data}


@router.post("/account/syncData")
async def sync_data(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    user = player_data(account)
    inventory = user.setdefault("inventory", {})
    for item_id in ("2001", "2002", "2003", "2004"):
        inventory.setdefault(item_id, 0)
    battles.reconcile_tutorial_state(user)
    gacha_service.ensure_gacha_state(user)
    missions.ensure_mission_state(user)
    checkin_service.ensure_state(user)
    social_service.ensure_state(user)
    status = user.setdefault("status", {})
    status["uid"] = str(account["uid"])
    status["nickName"] = account["nickname"] or "博士"
    status["serverName"] = runtime_settings.values()["server_name"]
    ts = now_ts()
    status["lastOnlineTs"] = ts
    status["lastRefreshTs"] = ts
    user.setdefault("pushFlags", {})["hasGifts"] = 1 if mail_service.unread_count(account["uid"]) else 0
    checkin = user.setdefault("checkIn", {})
    last_checkin = int(checkin.get("lastCheckInTs", 0) or 0)
    if not last_checkin or time.strftime("%Y-%m-%d", time.localtime(last_checkin)) != time.strftime("%Y-%m-%d", time.localtime(ts)):
        checkin["canCheckIn"] = 1
    save_player(account, user)
    return {"result": 0, "ts": ts, "user": user, **empty_delta()}


@router.post("/account/syncStatus")
async def sync_status(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    user = player_data(account)
    battles.reconcile_tutorial_state(user)
    gacha_service.ensure_gacha_state(user)
    missions.ensure_mission_state(user)
    ts = now_ts()
    status = user.setdefault("status", {})
    status["uid"] = str(account["uid"])
    status["nickName"] = account["nickname"] or "博士"
    status["serverName"] = runtime_settings.values()["server_name"]
    status["lastOnlineTs"] = ts
    status["lastRefreshTs"] = ts
    push = user.setdefault("pushFlags", {})
    push["hasGifts"] = 1 if mail_service.unread_count(account["uid"]) else 0
    params = body.get("params") if isinstance(body, dict) else None
    params = params if isinstance(params, dict) else {}
    purchase_param = params.get("16") or params.get(16) or {}
    good_map = purchase_param.get("goodIdMap") if isinstance(purchase_param, dict) else {}
    state = shop_service.purchase_state(user, good_map if isinstance(good_map, dict) else {})
    result = {
        "16": {
            "goodPurchaseState": {
                "result": state,
                "playerDataDelta": {"deleted": {}, "modified": {}},
            }
        }
    }
    modules = _as_int(body.get("modules"), 0) if isinstance(body, dict) else 0
    if modules & 2048:
        result["2048"] = {"gacha": [], "shop": []}
    save_player(account, user)
    modified = {
        key: user.get(key)
        for key in (
            "status",
            "pushFlags",
            "troop",
            "inventory",
            "gacha",
            "shop",
            "dungeon",
            "skin",
            "dexNav",
            "consumable",
            "recruit",
            "exgachadata",
            "mission",
        )
        if key in user
    }
    return {
        "result": result,
        "ts": ts,
        "pushMessage": [],
        "playerDataDelta": {"deleted": {}, "modified": modified},
    }


@router.post("/account/syncPushMessage")
async def sync_push_message(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    user = player_data(account)
    battles.reconcile_tutorial_state(user)
    gacha_service.ensure_gacha_state(user)
    missions.ensure_mission_state(user)
    ts = now_ts()
    status = user.setdefault("status", {})
    status["lastOnlineTs"] = ts
    status["lastRefreshTs"] = ts
    save_player(account, user)
    return {
        "result": 0,
        "now": ts,
        "next": ts + 60,
        "pushMessage": [],
        "playerDataDelta": {
            "deleted": {},
            "modified": {
                key: user.get(key)
                for key in ("status", "pushFlags", "troop", "inventory", "gacha", "shop", "dungeon", "skin", "dexNav", "consumable")
                if key in user
            },
        },
    }


@router.post("/quest/battleStart")
async def battle_start(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    template = json.loads(BATTLE_START_TEMPLATE.read_text(encoding="utf-8"))
    stage = str(body.get("stageId", "guide_01"))
    if not game_data.stage(stage):
        return {"result": 1, "error": f"unknown stage: {stage}"}
    # The client encrypts this value into battleFinish's battleId.  The official
    # server uses the stage id here, and the client sends it back unchanged.
    battle_id = stage
    template["battleId"] = battle_id
    template["result"] = 0
    modified = template.setdefault("playerDataDelta", {}).setdefault("modified", {})
    status = modified.setdefault("status", {})
    status["uid"] = str(account["uid"])
    status["nickName"] = account["nickname"] or "博士"
    status["serverName"] = runtime_settings.values()["server_name"]
    user = player_data(account)
    stages_user = user.setdefault("dungeon", {}).setdefault("stages", {})
    stage_node = stages_user.get(stage)
    if not isinstance(stage_node, dict):
        stage_node = {
            "hasBattleReplay": 0,
            "practiceTimes": 0,
            "state": 0,
            "completeTimes": 0,
            "stageId": stage,
            "startTimes": 0,
        }
        stages_user[stage] = stage_node
    stage_node["startTimes"] = int(stage_node.get("startTimes", 0) or 0) + 1
    save_player(account, user)
    battles.record_battle_start(account["id"], battle_id, stage, payload=body)
    stages = modified.setdefault("dungeon", {}).setdefault("stages", {})
    stages[stage] = stage_node
    return template


@router.post("/quest/battleFinish")
async def battle_finish(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    return battles.finish_battle(account, body)


@router.post("/quest/saveBattleReplay")
async def save_battle_replay(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    result = battles.save_battle_replay(user, body.get("battleReplay"))
    if result.get("result") == 0:
        save_player(account, user)
    return result


@router.post("/quest/getBattleReplay")
async def get_battle_replay(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    return battles.load_battle_replay(user, body.get("stageId"))


@router.post("/mission/confirmMission")
async def confirm_mission(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    result = missions.claim_missions(user, [body.get("missionId")], source="mission")
    save_player(account, user)
    return result


@router.post("/activity/actRewardOnly/getReward")
async def activity_reward_only_get_reward(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    activity_id = str(body.get("activityId") or "act1reward")
    pool_id = str(body.get("poolId") or "")
    table = game_data.excel("activity_table.json") or {}
    act = ((table.get("dynActs") or {}).get(activity_id) or {})
    pools = act.get("rewardPoolDataDict") or {}
    pool = pools.get(pool_id) or {}
    char_ids = [str(value) for value in pool.get("charIdList") or [] if value]
    if not char_ids:
        return {"result": 1, "error": "unknown reward pool"}
    user = player_data(account)
    activity = user.setdefault("activity", {})
    reward_only = activity.setdefault("REWARD_ONLY", {})
    state = reward_only.get(activity_id)
    if isinstance(state, dict) and state.get("hit"):
        return {"result": 1, "error": "reward already claimed"}
    owned = {
        str(char.get("charId") or "")
        for char in ((user.get("troop") or {}).get("chars") or {}).values()
        if isinstance(char, dict)
    }
    candidates = [char_id for char_id in char_ids if char_id not in owned] or char_ids
    char_id = random.choice(candidates)
    is_new = char_id not in owned
    items, delta = grants.apply_items(
        user, [{"id": char_id, "type": "CHAR", "count": 1}], source="reward-only"
    )
    reward_only[activity_id] = {"hit": char_id, "choice": pool_id}
    inst_id = 0
    for key, char in ((user.get("troop") or {}).get("chars") or {}).items():
        if isinstance(char, dict) and char.get("charId") == char_id:
            inst_id = _as_int(key, 0)
            break
    meta = game_data.character(char_id) or {}
    rarity = _as_int(str(meta.get("rarity") or "TIER_6").replace("TIER_", ""), 6)
    delta.setdefault("modified", {})["activity"] = user.get("activity", {})
    char_get = {
        "charId": char_id,
        "charInstId": inst_id,
        "isNew": 1 if is_new else 0,
        "rarity": rarity,
        "itemGet": [] if is_new else [{"id": f"p_{char_id}", "type": "MATERIAL", "count": 1}],
        "potent": None,
    }
    save_player(account, user)
    return {
        "result": 0,
        "charGet": char_get,
        "reward": items,
        "playerDataDelta": delta,
    }


@router.post("/social/getSortListInfo")
async def social_sort_list(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return social_service.sort_list(account, player_data(account), body)


@router.post("/social/searchPlayer")
async def social_search_player(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return social_service.search_players(account, player_data(account), body)


@router.post("/social/getFriendList")
async def social_friend_list(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return social_service.get_friend_list(account, player_data(account), body)


@router.post("/social/getFriendRequestList")
async def social_friend_request_list(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return social_service.get_friend_request_list(account, player_data(account), body)


@router.post("/social/getFriendAndRequestSendList")
async def social_friend_and_send_list(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    return social_service.friend_and_send_list(account, player_data(account))


@router.post("/social/sendFriendRequest")
async def social_send_request(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return social_service.send_friend_request(account, player_data(account), body)


@router.post("/social/processFriendRequest")
async def social_process_request(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return social_service.process_request(account, player_data(account), body)


@router.post("/social/deleteFriend")
async def social_delete_friend(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return social_service.delete_friend(account, player_data(account), body)


@router.post("/social/setAssistCharList")
async def social_set_assist(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return social_service.set_assist_chars(account, player_data(account), body)


@router.post("/social/setStarFriendList")
async def social_set_star(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return social_service.set_star_list(account, player_data(account), body)


@router.post("/social/setFriendAlias")
async def social_set_alias(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return social_service.set_alias(account, player_data(account), body)


@router.post("/social/getNameCardWithStatus")
async def social_name_card(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return social_service.name_card(account, player_data(account), body)


@router.post("/businessCard/getOtherPlayerNameCard")
async def other_player_name_card(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return social_service.name_card(account, player_data(account), body)


@router.post("/social/receiveSocialPoint")
async def receive_social_point(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    user = player_data(account)
    items, delta = grants.apply_items(
        user,
        [{"type": "SOCIAL_PT", "id": "SOCIAL_PT", "count": 100}],
        source="social-point",
    )
    missions.record_mission_event(
        user,
        "ReceiveSocialPoint",
        amount_for=lambda entry: 100
        if missions._mission_target(entry) >= 100
        else 1,
    )
    delta.setdefault("modified", {})["mission"] = user.get("mission", {})
    save_player(account, user)
    return {
        "result": 0,
        "reward": items,
        "playerDataDelta": delta,
    }


@router.post("/mission/confirmMissionList")
async def confirm_mission_list(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    ids = body.get("missionIds") or body.get("missionIdList") or body.get("missions") or []
    if isinstance(ids, str):
        ids = [ids]
    user = player_data(account)
    result = missions.claim_missions(user, ids, source="mission-list")
    save_player(account, user)
    return result


@router.post("/mission/autoConfirmMissions")
async def auto_confirm_missions(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    result = missions.auto_confirm(user, body.get("type") or "MAIN")
    save_player(account, user)
    return result


@router.post("/mission/confirmMissionGroup")
async def confirm_mission_group(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    result = missions.confirm_group(user, str(body.get("missionGroupId") or ""))
    if result.get("result") == 0:
        save_player(account, user)
    return result


@router.post("/mission/exchangeMissionRewards")
async def exchange_mission_rewards(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    result = missions.exchange_reward(user, str(body.get("targetRewardsId") or ""))
    if result.get("result") == 0:
        save_player(account, user)
    return result


@router.post("/user/checkIn")
async def user_checkin(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    user = player_data(account)
    checkin = user.setdefault("checkIn", {})
    ts = now_ts()
    last_checkin = int(checkin.get("lastCheckInTs", 0) or 0)
    today = time.strftime("%Y-%m-%d", time.localtime(ts))
    if last_checkin and time.strftime("%Y-%m-%d", time.localtime(last_checkin)) == today:
        monthly_granted, monthly_delta = monthly_card.grant_daily(user, now=ts)
        delta = {"deleted": {}, "modified": {"checkIn": checkin_delta(checkin)}}
        checkin_service.ensure_state(user)
        delta["modified"]["openServerV3"] = user.get("openServerV3")
        grants.merge_delta(delta, monthly_delta)
        save_player(account, user)
        return {
            "result": 0,
            "signInRewards": [],
            "subscriptionRewards": monthly_granted,
            "playerDataDelta": delta,
        }
    group_id = str(checkin.get("checkInGroupId") or "signin99")
    history = checkin.get("checkInHistory")
    if not isinstance(history, list):
        history = []
        checkin["checkInHistory"] = history
    index = len(history)
    reward = game_data.checkin_reward(group_id, index)
    if not reward:
        reward = {"id": "4001", "type": "GOLD", "count": 2000}
    granted, delta = grants.apply_items(user, [reward], source="checkin", now=ts)
    history.insert(0, 0)
    checkin["canCheckIn"] = 0
    checkin["checkInHistory"] = history
    checkin["showCount"] = int(checkin.get("showCount", 0) or 0) + 1
    checkin["checkInRewardIndex"] = int(checkin.get("checkInRewardIndex", 0) or 0) + 1
    checkin["lastCheckInTs"] = ts
    delta["modified"]["checkIn"] = checkin_delta(checkin)
    checkin_service.ensure_state(user)
    delta["modified"]["openServerV3"] = user.get("openServerV3")
    monthly_granted, monthly_delta = monthly_card.grant_daily(user, now=ts)
    grants.merge_delta(delta, monthly_delta)
    save_player(account, user)
    return {
        "result": 0,
        "signInRewards": granted,
        "subscriptionRewards": monthly_granted,
        "playerDataDelta": delta,
    }


@router.post("/user/exchangeDiamondShard")
async def exchange_diamond_shard(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    count = _as_int(body.get("count"), 0)
    if count <= 0:
        return {"result": 1, "errMsg": "兑换数量无效"}
    user = player_data(account)
    status = user.setdefault("status", {})
    diamond_key = grants.diamond_status_key(user, account.get("platform"))
    owned = _as_int(status.get(diamond_key), 0)
    if owned < count:
        return {"result": 1, "errMsg": "剩余源石无法兑换合成玉"}
    rate = _as_int((game_data.excel("gamedata_const.json") or {}).get("diamondToShdRate"), 180)
    status[diamond_key] = owned - count
    status["diamondShard"] = _as_int(status.get("diamondShard"), 0) + count * rate
    save_player(account, user)
    logger.info(
        "exchangeDiamondShard uid=%s platform=%s count=%s rate=%s %s=%s shard=%s",
        account["uid"], diamond_key, count, rate,
        diamond_key, status[diamond_key], status["diamondShard"],
    )
    return {
        "result": 0,
        "playerDataDelta": {
            "deleted": {},
            "modified": {
                "status": {
                    "androidDiamond": _as_int(status.get("androidDiamond"), 0),
                    "iosDiamond": _as_int(status.get("iosDiamond"), 0),
                    "diamondShard": status["diamondShard"],
                }
            },
        },
    }


def _open_server_v3_claim(account, body):
    user = player_data(account)
    v3 = user.setdefault("openServerV3", {})
    if not isinstance(v3, dict):
        v3 = {}
        user["openServerV3"] = v3
    checkin, history = checkin_service.ensure_state(user)
    group_id = str(checkin.get("groupId") or "new_login_g_1")
    day_count = max(1, len(
        (game_data.open_server_v3_checkin_group(group_id) or {}).get("checkInItemList") or []
    ))

    index = _as_int(body.get("index"), -1)
    if index < 0:
        # The client sends -1 when the page opens and expects the next
        # unclaimed day to be awarded.
        index = next((i for i, value in enumerate(history) if value == 1), -1)
    if index < 0 or index >= day_count:
        return {
            "result": 0,
            "items": [],
            "playerDataDelta": {
                "deleted": {},
                "modified": {"openServerV3": v3},
            },
            "pushMessage": [],
        }
    while len(history) <= index:
        history.append(1)

    logger.info("openServerV3 checkin uid=%s index=%s history=%s", account["uid"], index, history)
    if history[index] == 0:
        return {
            "result": 0,
            "items": [],
            "playerDataDelta": {
                "deleted": {},
                "modified": {"openServerV3": v3},
            },
            "pushMessage": [],
        }

    rewards = game_data.open_server_v3_checkin_rewards(group_id, index)
    if not rewards:
        return {
            "result": 0,
            "items": [],
            "playerDataDelta": {"deleted": {}, "modified": {}},
            "pushMessage": [],
        }
    history[index] = 0
    granted, delta = grants.apply_items(user, rewards, source="open-server-v3-checkin", now=now_ts())
    delta.setdefault("deleted", {})
    modified = delta.setdefault("modified", {})
    modified["openServerV3"] = v3
    save_player(account, user)
    response = {
        "result": 0,
        "items": granted,
        "playerDataDelta": delta,
        "pushMessage": [],
    }
    if granted:
        response["reward"] = granted[0]
    return response


def _open_server_v3_milestone(account, body):
    user = player_data(account)
    v3 = user.setdefault("openServerV3", {})
    if not isinstance(v3, dict):
        v3 = {}
        user["openServerV3"] = v3
    mission = v3.setdefault("mission", {})
    if not isinstance(mission, dict):
        mission = {}
        v3["mission"] = mission
    milestone = mission.setdefault("milestone", {})
    if not isinstance(milestone, dict):
        milestone = {}
        mission["milestone"] = milestone
    group_id = str(milestone.get("groupId") or "new_bp_g_1")
    milestone["groupId"] = group_id
    point = max(0, _as_int(milestone.get("point"), 0))
    got = milestone.get("got")
    if not isinstance(got, list):
        got = []
        milestone["got"] = got
    got_levels = set()
    for value in got:
        try:
            got_levels.add(int(value))
        except (TypeError, ValueError):
            continue
    groups = game_data.open_server_v3_data().get("milestoneGroupData") or {}
    group = groups.get(group_id) or {}
    entries = [row for row in group.get("itemList") or [] if isinstance(row, dict)]
    current_level = 0
    for row in entries:
        level = _as_int(row.get("level"), 0)
        need = _as_int(row.get("pointCnt"), 0)
        if need <= point:
            current_level = max(current_level, level)
    requested = body.get("levels")
    if isinstance(requested, list):
        wanted = {_as_int(value, 0) for value in requested}
        wanted.discard(0)
    elif requested is not None:
        wanted = {_as_int(requested, 0)}
        wanted.discard(0)
    else:
        wanted = set()
    rewards = []
    claimed = []
    for row in entries:
        level = _as_int(row.get("level"), 0)
        if level <= 0 or level > current_level or level in got_levels:
            continue
        if wanted and level not in wanted:
            continue
        reward = row.get("reward") or {}
        item = {
            "id": str(reward.get("id") or ""),
            "type": str(reward.get("type") or ""),
            "count": _as_int(reward.get("count"), 0),
        }
        if not item["id"] or not item["type"] or item["count"] <= 0:
            continue
        rewards.append(item)
        claimed.append(level)
    granted, delta = grants.apply_items(
        user, rewards, source="open-server-v3-milestone", now=now_ts()
    )
    got_levels.update(claimed)
    milestone["got"] = sorted(got_levels)
    delta.setdefault("deleted", {})
    delta.setdefault("modified", {})["openServerV3"] = v3
    save_player(account, user)
    response = {
        "result": 0,
        "items": granted,
        "playerDataDelta": delta,
        "pushMessage": [],
    }
    if granted:
        response["reward"] = granted[0]
    return response


@router.post("/openServerV3/getCheckInReward")
async def open_server_v3_checkin_reward(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return _open_server_v3_claim(account, body)


@router.post("/openServerV3/getMilestoneReward")
async def open_server_v3_milestone_reward(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return _open_server_v3_milestone(account, body)


@router.post("/activity/getOpenServerMilestoneReward")
async def activity_open_server_milestone(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return _open_server_v3_milestone(account, body)


@router.post("/activity/getOpenServerCheckInReward")
async def activity_open_server_checkin_reward(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    return _open_server_v3_claim(account, body)


@router.post("/mail/getMetaInfoList")
async def mail_meta_list(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    return {"result": mail_service.meta_list(account["uid"])}


@router.post("/mail/listMailBox")
async def mail_list_box(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    ids = body.get("mailIdList") or body.get("sysMailIdList") or []
    return {
        "mailList": mail_service.list_mailbox(account["uid"], ids),
        "surveyMailList": [],
        **empty_delta(),
    }


@router.post("/mail/receiveMail")
async def mail_receive(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        mail_id = int(body.get("mailId") or 0)
    except (TypeError, ValueError):
        mail_id = 0
    return mail_service.receive_mail(account, mail_id)


@router.post("/mail/receiveAllMail")
async def mail_receive_all(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    ids = body.get("mailIdList") or body.get("sysMailIdList") or []
    return mail_service.receive_all(account, ids)


@router.post("/mail/removeAllReceivedMail")
async def mail_remove_all(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    ids = body.get("mailIdList") or body.get("sysMailIdList") or []
    mail_service.remove_mail(account["uid"], ids, received_only=True)
    return {"result": 0, **empty_delta()}


@router.post("/mailCollection/getList")
async def mail_collection_list(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    return {"collections": [], "extra": []}


@router.post("/quest/squadFormation")
async def squad_formation(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    squad_id = str(body.get("squadId") or "0")
    slots = body.get("slots")
    if not isinstance(slots, list):
        slots = []
    user = player_data(account)
    chars = user.get("troop", {}).get("chars") or {}
    normalized = []
    for slot in slots[:12]:
        if not isinstance(slot, dict):
            normalized.append(None)
            continue
        inst_id = _as_int(slot.get("charInstId"), -1)
        if inst_id < 0 or str(inst_id) not in chars:
            normalized.append(None)
            continue
        char = chars.get(str(inst_id)) or {}
        normalized_slot = {
            "charInstId": inst_id,
            "skillIndex": _as_int(
                slot.get("skillIndex"),
                _as_int(char.get("defaultSkillIndex"), -1),
            ),
            "currentEquip": slot.get("currentEquip", char.get("currentEquip")),
        }
        if slot.get("currentTmpl") is not None:
            normalized_slot["currentTmpl"] = slot.get("currentTmpl")
        if isinstance(slot.get("tmpl"), (dict, list)):
            normalized_slot["tmpl"] = slot.get("tmpl")
        normalized.append(normalized_slot)
    normalized.extend([None] * (12 - len(normalized)))
    squads = user.setdefault("troop", {}).setdefault("squads", {})
    squad = squads.get(squad_id)
    if not isinstance(squad, dict):
        squad = {"squadId": squad_id, "name": "", "slots": []}
        squads[squad_id] = squad
    squad["squadId"] = squad_id
    squad["slots"] = normalized
    mission_changed = bool(missions.record_mission_event(user, "SquadFormation"))
    save_player(account, user)
    modified = {"troop": user.get("troop", {})}
    if mission_changed:
        modified["mission"] = user.get("mission", {})
    return {
        "result": 0,
        "playerDataDelta": {"deleted": {}, "modified": modified},
    }


@router.post("/quest/changeSquadName")
async def change_squad_name(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    squad_id = str(body.get("squadId") or "0")
    name = str(body.get("name") or "")[:64]
    user = player_data(account)
    squads = user.setdefault("troop", {}).setdefault("squads", {})
    squad = squads.get(squad_id)
    if not isinstance(squad, dict):
        squad = {"squadId": squad_id, "slots": [None] * 12}
        squads[squad_id] = squad
    squad["squadId"] = squad_id
    squad["name"] = name
    save_player(account, user)
    return {
        "result": 0,
        "playerDataDelta": {"deleted": {}, "modified": {"troop": user.get("troop", {})}},
    }


@router.post("/pay/getUnconfirmedOrderIdList")
async def unconfirmed_orders():
    return {"result": 0, "orderIdList": []}


@router.post("/pay/createOrder")
async def create_order(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    order = payments.create_order(
        account,
        body.get("storeId"),
        body.get("goodId"),
        body.get("platform"),
    )
    if order and int(order.get("status") or 0) == 2:
        return {
            "result": 1,
            "error": order.get("reject_reason") or "order rejected",
            "orderId": order["order_id"],
        }
    return payments.create_order_response(order)


@router.post("/pay/confirmOrder")
async def confirm_order(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    return payments.confirm_order(account, str(body.get("orderId", "")))


def _order_id_from_body(account, body):
    order_id = str(body.get("orderId") or body.get("outTradeNo") or "")
    if order_id:
        return order_id
    order = payments.latest_order(account)
    return order["order_id"] if order else ""


def _sdk_account(request, body):
    return auth(request) or account_by_token(str(body.get("token", "")))


@router.post("/pay/createOrderAlipay")
async def create_order_alipay(request: Request):
    body = await request.json()
    account = _sdk_account(request, body)
    if not account:
        return {"status": 1, "msg": "Invalid Token", "type": "A"}
    return payments.alipay_page_pay(account, _order_id_from_body(account, body))


@router.post("/pay/createOrderWechat")
async def create_order_wechat(request: Request):
    body = await request.json()
    account = _sdk_account(request, body)
    if not account:
        return {"status": 1, "msg": "Invalid Token", "type": "A"}
    order = payments.order_by_id(_order_id_from_body(account, body))
    if not order or order["account_id"] != account["id"]:
        return {"status": 1, "msg": "order not found", "type": "A"}
    return {
        "status": 0,
        "msg": "OK",
        "type": "A",
        "requestObj": {
            "appid": "local",
            "partnerid": "local",
            "prepayid": order["order_id"],
            "package": "Sign=WXPay",
            "noncestr": order["order_id"],
            "timestamp": str(order["created_at"]),
            "sign": "local",
        },
        "orderId": order["order_id"],
    }


@router.post("/pay/confirmOrderAlipay")
async def confirm_order_alipay(request: Request):
    body = await request.json()
    account = _sdk_account(request, body)
    if not account:
        return {"status": 1, "msg": "Invalid Token", "type": "A"}
    order_id = _order_id_from_body(account, body)
    return payments.confirm_alipay(account, order_id, str(body.get("result", "")), int(body.get("status", 0) or 0) == 0)


@router.post("/pay/confirmOrderWechat")
async def confirm_order_wechat(request: Request):
    body = await request.json()
    account = _sdk_account(request, body)
    if not account:
        return {"status": 1, "msg": "Invalid Token", "type": "A"}
    order_id = _order_id_from_body(account, body)
    return payments.confirm_alipay(account, order_id, "", int(body.get("status", 0) or 0) == 0)


@router.post("/pay/order/v2/check/{payment}")
async def check_order_v2(payment: str, request: Request):
    body = await request.json()
    order_id = str(body.get("orderId", ""))
    order = payments.order_by_id(order_id)
    if not order:
        return {"status": 1, "msg": "order not found", "type": "A"}
    return {
        "status": 0,
        "msg": "OK",
        "type": "A",
        "data": {"orderId": order_id, "payment": payment, "paid": int(order["status"] or 0) == 1},
    }


@router.get("/pay/success", response_class=HTMLResponse)
async def pay_success(orderId: str = ""):
    if orderId:
        payments.mark_paid(orderId)
    return HTMLResponse(
        "<html><head><meta charset='utf-8'><title>支付成功</title></head>"
        "<body style='background:#111;color:#eee;font-family:sans-serif;text-align:center;padding-top:20vh'>"
        "<h1>支付成功</h1><p>本地测试订单已回调，正在返回游戏。</p>"
        "<script>setTimeout(function(){location.href='hypergryph://pay_return'},300)</script>"
        "</body></html>"
    )


@router.get("/pay/mock/alipay")
async def mock_alipay(orderId: str = ""):
    target = f"/pay/success?orderId={orderId}" if orderId else "/pay/success"
    return RedirectResponse(target, status_code=302)


@router.get("/pay/order/v1/state")
async def order_state(orderId: str = ""):
    return payments.order_state(orderId)


@router.post("/story/finishStory")
async def finish_story():
    return {"result": 0, **empty_delta()}


@router.post("/shop/getCashGoodList")
async def cash_good_list():
    return shop_response("CashGoodList.json")


@router.post("/shop/getLowGoodList")
async def low_good_list():
    return shop_response("LowGoodList.json")


@router.post("/shop/getHighGoodList")
async def high_good_list():
    return shop_response("HighGoodList.json")


@router.post("/shop/getExtraGoodList")
async def extra_good_list():
    return shop_response("ExtraGoodList.json")


@router.post("/shop/getGPGoodList")
async def gp_good_list(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    user = player_data(account)
    payload = shop_service.decorate_gp_payload(user)
    payload["playerDataDelta"] = {"deleted": {}, "modified": {}}
    return payload


@router.post("/shop/getLMTGSGoodList")
async def lmtgs_good_list():
    return shop_response("LMTGSGoodList.json")


@router.post("/shop/getEPGSGoodList")
async def epgs_good_list():
    return shop_response("EPGSGoodList.json")


@router.post("/shop/getRepGoodList")
async def rep_good_list():
    return shop_response("RepGoodList.json")


@router.post("/shop/getSocialGoodList")
async def social_good_list(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    user = player_data(account)
    return shop_service.decorate_social_payload(user)


@router.post("/shop/getClassicGoodList")
async def classic_good_list():
    return shop_response("ClassicGoodList.json")


@router.post("/shop/getSkinGoodList")
async def skin_good_list():
    return load_skin_goods()


@router.post("/shop/getFurniGoodList")
async def furni_good_list():
    return shop_response("FurniGoodList.json")


@router.post("/shop/getGoodPurchaseState")
async def good_purchase_state(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    user = player_data(account)
    requested = body.get("goodIdMap") if isinstance(body, dict) else {}
    if not isinstance(requested, dict):
        requested = body if isinstance(body, dict) else {}
    return {
        "result": shop_service.purchase_state(user, requested),
        "playerDataDelta": {"deleted": {}, "modified": {}},
    }


def _shop_platform(account, body):
    user = player_data(account)
    return (
        grants.normalize_platform((body or {}).get("platform"))
        or grants.normalize_platform(account.get("platform"))
        or grants.normalize_platform(user.get("localPlatform"))
        or "android"
    )


@router.post("/shop/buyGPGood")
async def buy_gp_good(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    user = player_data(account)
    platform = _shop_platform(account, body)
    result = shop_service.buy_gp(user, body.get("goodId"), platform=platform)
    if result.get("result") == 0:
        save_player(account, user)
    return result


@router.post("/shop/buyGPGoodWithTicket")
async def buy_gp_good_with_ticket(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    user = player_data(account)
    platform = _shop_platform(account, body)
    result = shop_service.buy_gp_with_ticket(
        user,
        body.get("goodId"),
        ticket_id=body.get("ticketId"),
        platform=platform,
    )
    if result.get("result") == 0:
        save_player(account, user)
    return result


def _char_by_inst(user, inst_id):
    chars = (user.get("troop") or {}).get("chars") or {}
    return chars.get(str(inst_id)), chars


def _sync_skill_unlocks(char, meta):
    phase = _as_int(char.get("evolvePhase"), 0)
    skills = char.get("skills")
    if not isinstance(skills, list):
        skills = []
        char["skills"] = skills
    meta_skills = {str(skill.get("skillId")): skill for skill in meta.get("skills") or [] if isinstance(skill, dict)}
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        source = meta_skills.get(str(skill.get("skillId"))) or {}
        needed = str((source.get("unlockCond") or {}).get("phase") or "PHASE_0")
        needed_phase = _as_int(needed.replace("PHASE_", ""), 0)
        skill["unlock"] = 1 if phase >= needed_phase else 0
    unlocked = [index for index, skill in enumerate(skills) if isinstance(skill, dict) and _as_int(skill.get("unlock"), 0)]
    if _as_int(char.get("defaultSkillIndex"), -1) not in unlocked:
        char["defaultSkillIndex"] = unlocked[0] if unlocked else -1


def _char_response(
    user,
    char_inst_id=None,
    *,
    char_fields=None,
    full_char=False,
    char_inst_ids=None,
    inventory_ids=(),
    status_keys=(),
):
    """Build the partial PlayerDataDelta shape used by charBuild endpoints.

    The client's incremental delta applier expects ``troop.chars`` to contain
    only the changed character entry.  Sending the whole ``troop`` object made
    the upgrade page keep its cached character until the page was left and
    reopened, even though the server had already persisted the new level.
    """
    modified = {}
    ids = list(char_inst_ids or ([] if char_inst_id is None else [char_inst_id]))
    if ids:
        chars = ((user.get("troop") or {}).get("chars") or {})
        changed = {}
        for raw_id in ids:
            inst_id = str(raw_id)
            char = chars.get(inst_id)
            if not isinstance(char, dict):
                continue
            if full_char or char_fields is None:
                payload = char
            else:
                payload = {key: char.get(key) for key in char_fields}
            changed[inst_id] = payload
        if changed:
            modified["troop"] = {"chars": changed}
    if inventory_ids:
        inventory = user.get("inventory") or {}
        modified["inventory"] = {
            str(item_id): _as_int(inventory.get(str(item_id)), 0) for item_id in inventory_ids
        }
    if status_keys:
        status = user.get("status") or {}
        modified["status"] = {str(key): status.get(key) for key in status_keys}
    return {"result": 0, "playerDataDelta": {"deleted": {}, "modified": modified}}


def _gamedata_const():
    return game_data.excel("gamedata_const.json") or {}


def _char_curves(char_id):
    meta = game_data.character(char_id) or {}
    rarity = _as_int(str(meta.get("rarity") or "TIER_1").replace("TIER_", ""), 1)
    index = max(0, 5 - rarity)
    const = _gamedata_const()
    exp_maps = const.get("characterExpMap") or []
    gold_maps = const.get("characterUpgradeCostMap") or []
    exp = exp_maps[index] if index < len(exp_maps) else (exp_maps[-1] if exp_maps else [])
    gold = gold_maps[index] if index < len(gold_maps) else (gold_maps[-1] if gold_maps else [])
    return meta, rarity, exp, gold


def _phase_max_level(meta, phase):
    phases = meta.get("phases") or []
    if not phases:
        return 90
    phase = min(max(0, phase), len(phases) - 1)
    return max(1, _as_int((phases[phase] or {}).get("maxLevel"), 1))


@router.post("/charBuild/upgradeChar")
async def char_build_upgrade_char(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    guide_before = _guide_snapshot(user)
    inst_id = str(body.get("charInstId") or "")
    char, _ = _char_by_inst(user, inst_id)
    if not isinstance(char, dict):
        return {"result": 1, "error": "char not found"}
    meta, _rarity, exp_curve, gold_curve = _char_curves(char.get("charId"))
    max_level = _phase_max_level(meta, _as_int(char.get("evolvePhase"), 0))
    level = max(1, min(_as_int(char.get("level"), 1), max_level))
    if level >= max_level:
        return {"result": 1, "error": "该干员已达最大等级"}
    total_exp = max(0, _as_int(char.get("exp"), 0))
    inventory = user.setdefault("inventory", {})
    exp_values = {"2001": 200, "2002": 400, "2003": 1000, "2004": 2000}
    gained = 0
    used_item_ids = []
    requested_items = []
    for raw in body.get("expMats") or []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or "")
        count = max(0, _as_int(raw.get("count"), 0))
        if item_id not in exp_values or count <= 0:
            continue
        have = _as_int(inventory.get(item_id), 0)
        if have < count:
            return {"result": 1, "error": f"{item_id} 不足"}
        requested_items.append((item_id, count))
        if item_id not in used_item_ids:
            used_item_ids.append(item_id)
        gained += exp_values[item_id] * count
    if gained <= 0:
        return {"result": 1, "error": "没有可使用的经验卡"}
    for item_id, count in requested_items:
        have = _as_int(inventory.get(item_id), 0)
        if have == count:
            inventory.pop(item_id, None)
        else:
            inventory[item_id] = have - count
    total_exp += gained
    old_level = level
    gold = _as_int((user.get("status") or {}).get("gold"), 0)
    while level < max_level:
        need = _as_int(exp_curve[level - 1] if level - 1 < len(exp_curve) else -1, -1)
        cost = _as_int(gold_curve[level - 1] if level - 1 < len(gold_curve) else -1, -1)
        if need < 0 or cost < 0 or total_exp < need or gold < cost:
            break
        total_exp -= need
        gold -= cost
        level += 1
    user.setdefault("status", {})["gold"] = gold
    char["level"] = level
    char["exp"] = total_exp
    mission_changed = False
    if level > old_level:
        mission_changed = bool(missions.record_mission_event(user, "UpgradeChar"))
    if _guide_changed(user, guide_before):
        mission_changed = True
    save_player(account, user)
    response = _char_response(
        user,
        inst_id,
        char_fields=("level", "exp", "favorPoint"),
        inventory_ids=used_item_ids,
        status_keys=("gold",),
    )
    if mission_changed:
        response["playerDataDelta"]["modified"]["mission"] = user.get("mission", {})
    return response


@router.post("/charBuild/evolveChar")
async def char_build_evolve_char(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    guide_before = _guide_snapshot(user)
    inst_id = str(body.get("charInstId") or "")
    char, _ = _char_by_inst(user, inst_id)
    if not isinstance(char, dict):
        return {"result": 1, "error": "char not found"}
    dest = body.get("destEvolvePhase")
    if isinstance(dest, str):
        dest = dest.replace("PHASE_", "")
    dest = max(0, _as_int(dest, 0))
    meta, rarity, _exp, _gold = _char_curves(char.get("charId"))
    phases = meta.get("phases") or []
    current = _as_int(char.get("evolvePhase"), 0)
    if dest <= current or dest >= max(1, len(phases)):
        return {"result": 1, "error": "invalid evolve phase"}
    costs = _gamedata_const().get("evolveGoldCost") or []
    row = costs[rarity - 1] if 0 < rarity <= len(costs) else []
    cost = _as_int(row[dest - 1] if 0 < dest <= len(row) else 0, 0)
    inventory = user.setdefault("inventory", {})
    used_item_ids = []
    for raw in (phases[dest] or {}).get("evolveCost") or []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or "")
        count = _as_int(raw.get("count"), 0)
        if not item_id or count <= 0:
            continue
        have = _as_int(inventory.get(item_id), 0)
        if have < count:
            return {"result": 1, "error": f"{item_id} 不足"}
        if item_id not in used_item_ids:
            used_item_ids.append(item_id)
    for raw in (phases[dest] or {}).get("evolveCost") or []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or "")
        count = _as_int(raw.get("count"), 0)
        if not item_id or count <= 0:
            continue
        have = _as_int(inventory.get(item_id), 0)
        if have == count:
            inventory.pop(item_id, None)
        else:
            inventory[item_id] = have - count
    status = user.setdefault("status", {})
    if cost > 0 and _as_int(status.get("gold"), 0) < cost:
        return {"result": 1, "error": "龙门币不足"}
    if cost > 0:
        status["gold"] = _as_int(status.get("gold"), 0) - cost
    char["evolvePhase"] = dest
    char["level"] = min(_as_int(char.get("level"), 1), _phase_max_level(meta, dest))
    _sync_skill_unlocks(char, meta)
    guide_changed = _guide_changed(user, guide_before)
    save_player(account, user)
    response = _char_response(
        user,
        inst_id,
        full_char=True,
        inventory_ids=used_item_ids,
        status_keys=("gold",),
    )
    if guide_changed:
        response["playerDataDelta"]["modified"]["mission"] = user.get("mission", {})
    return response


@router.post("/charBuild/evolveCharUseItem")
async def char_build_evolve_use_item(request: Request):
    return await char_build_evolve_char(request)


@router.post("/charBuild/upgradeCharLevelMaxUseItem")
async def char_build_level_max_item(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    guide_before = _guide_snapshot(user)
    inst_id = str(body.get("charInsId") or body.get("charInstId") or "")
    char, _ = _char_by_inst(user, inst_id)
    if not isinstance(char, dict):
        return {"result": 1, "error": "char not found"}
    item_id = str(body.get("itemId") or "")
    if item_id:
        inventory = user.setdefault("inventory", {})
        have = _as_int(inventory.get(item_id), 0)
        if have <= 0:
            return {"result": 1, "error": "item not found"}
        if have == 1:
            inventory.pop(item_id, None)
        else:
            inventory[item_id] = have - 1
    char["level"] = _phase_max_level(game_data.character(char.get("charId")) or {}, _as_int(char.get("evolvePhase"), 0))
    char["exp"] = 0
    guide_changed = _guide_changed(user, guide_before)
    save_player(account, user)
    response = _char_response(
        user,
        inst_id,
        char_fields=("level", "exp"),
        inventory_ids=(item_id,) if item_id else (),
    )
    if guide_changed:
        response["playerDataDelta"]["modified"]["mission"] = user.get("mission", {})
    return response


@router.post("/charBuild/upgradeSkill")
async def char_build_upgrade_skill(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    guide_before = _guide_snapshot(user)
    inst_id = str(body.get("charInstId") or "")
    char, _ = _char_by_inst(user, inst_id)
    if not isinstance(char, dict):
        return {"result": 1, "error": "char not found"}
    target = max(1, min(7, _as_int(body.get("targetLevel"), 1)))
    meta = game_data.character(char.get("charId")) or {}
    chain = meta.get("allSkillLvlup") or []
    index = target - 2
    used_item_ids = []
    if index >= 0 and index < len(chain):
        inventory = user.setdefault("inventory", {})
        for cost in (chain[index] or {}).get("lvlUpCost") or []:
            item_id = str(cost.get("id") or "")
            count = _as_int(cost.get("count"), 0)
            if not item_id or count <= 0:
                continue
            have = _as_int(inventory.get(item_id), 0)
            if have < count:
                return {"result": 1, "error": f"{item_id} 不足"}
            if have == count:
                inventory.pop(item_id, None)
            else:
                inventory[item_id] = have - count
            if item_id not in used_item_ids:
                used_item_ids.append(item_id)
    char["mainSkillLvl"] = target
    mission_changed = bool(missions.record_mission_event(user, "UpgradeSkill"))
    if _guide_changed(user, guide_before):
        mission_changed = True
    save_player(account, user)
    response = _char_response(
        user,
        inst_id,
        char_fields=("mainSkillLvl",),
        inventory_ids=used_item_ids,
    )
    if mission_changed:
        response["playerDataDelta"]["modified"]["mission"] = user.get("mission", {})
    return response


@router.post("/charBuild/boostPotential")
async def char_build_boost_potential(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    inst_id = str(body.get("charInstId") or "")
    char, _ = _char_by_inst(user, inst_id)
    if not isinstance(char, dict):
        return {"result": 1, "error": "char not found"}
    item_id = str(body.get("itemId") or "")
    target = max(0, min(5, _as_int(body.get("targetRank"), 0)))
    inventory = user.setdefault("inventory", {})
    amount = 1 if "_char_" in item_id else 4
    have = _as_int(inventory.get(item_id), 0) if item_id else 0
    if item_id and have < amount:
        return {"result": 1, "error": "potential item not enough"}
    if item_id:
        if have == amount:
            inventory.pop(item_id, None)
        else:
            inventory[item_id] = have - amount
    char["potentialRank"] = target
    save_player(account, user)
    return _char_response(
        user,
        inst_id,
        char_fields=("potentialRank",),
        inventory_ids=(item_id,) if item_id else (),
    )


@router.post("/charBuild/setDefaultSkill")
async def char_build_set_default_skill(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    inst_id = str(body.get("charInstId") or "")
    char, _ = _char_by_inst(user, inst_id)
    if not isinstance(char, dict):
        return {"result": 1, "error": "char not found"}
    index = _as_int(body.get("defaultSkillIndex"), -1)
    skills = char.get("skills") or []
    if index < 0 or index >= len(skills) or not _as_int((skills[index] or {}).get("unlock"), 0):
        return {"result": 1, "error": "skill not unlocked"}
    char["defaultSkillIndex"] = index
    save_player(account, user)
    return _char_response(user, inst_id, char_fields=("defaultSkillIndex",))


@router.post("/charBuild/setEquipment")
async def char_build_set_equipment(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    inst_id = str(body.get("charInstId") or "")
    char, _ = _char_by_inst(user, inst_id)
    if not isinstance(char, dict):
        return {"result": 1, "error": "char not found"}
    equip_id = str(body.get("equipId") or body.get("equip") or "")
    char["currentEquip"] = equip_id or None
    save_player(account, user)
    return _char_response(user, inst_id, char_fields=("currentEquip",))


@router.post("/charBuild/unlockEquipment")
async def char_build_unlock_equipment(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    inst_id = str(body.get("charInstId") or "")
    char, _ = _char_by_inst(user, inst_id)
    if not isinstance(char, dict):
        return {"result": 1, "error": "char not found"}
    equip_id = str(body.get("equipId") or "")
    if equip_id:
        node = char.setdefault("equip", {}).setdefault(equip_id, {})
        node["locked"] = 0
        node.setdefault("level", 1)
        node.setdefault("hide", 0)
    save_player(account, user)
    return _char_response(user, inst_id, char_fields=("equip",))


@router.post("/charBuild/upgradeEquipment")
async def char_build_upgrade_equipment(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    inst_id = str(body.get("charInstId") or "")
    char, _ = _char_by_inst(user, inst_id)
    if not isinstance(char, dict):
        return {"result": 1, "error": "char not found"}
    equip_id = str(body.get("equipId") or "")
    if equip_id:
        node = char.setdefault("equip", {}).setdefault(equip_id, {})
        node["locked"] = 0
        node["level"] = max(1, min(3, _as_int(body.get("targetLevel"), _as_int(node.get("level"), 1))))
    save_player(account, user)
    return _char_response(user, inst_id, char_fields=("equip",))


@router.post("/charBuild/setCharVoiceLan")
async def char_build_set_voice(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    voice = str(body.get("voiceLan") or "CN_MANDARIN")
    ids = body.get("charList") or []
    if not isinstance(ids, list):
        ids = [ids]
    chars = (user.get("troop") or {}).get("chars") or {}
    for inst_id in ids:
        char = chars.get(str(inst_id))
        if isinstance(char, dict):
            char["voiceLan"] = voice
    save_player(account, user)
    return _char_response(user, char_inst_ids=ids, char_fields=("voiceLan",))


@router.post("/charBuild/batchSetCharVoiceLan")
async def char_build_batch_voice(request: Request):
    return await char_build_set_voice(request)


@router.post("/charBuild/changeSkinSpState")
async def char_build_change_skin(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    inst_id = str(body.get("charInstId") or "")
    char, _ = _char_by_inst(user, inst_id)
    if isinstance(char, dict):
        skin = body.get("skinId") or body.get("skin")
        if skin:
            char["skin"] = str(skin)
        save_player(account, user)
    return _char_response(user, inst_id, char_fields=("skin",))


@router.post("/charBuild/upgradeSpecializedSkillUseItem")
async def char_build_specialize(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    guide_before = _guide_snapshot(user)
    inst_id = str(body.get("charInstId") or "")
    char, _ = _char_by_inst(user, inst_id)
    if isinstance(char, dict):
        skills = char.get("skills") or []
        index = _as_int(body.get("skillIndex"), 0)
        if 0 <= index < len(skills) and isinstance(skills[index], dict):
            skills[index]["specializeLevel"] = max(0, min(3, _as_int(body.get("targetLevel"), 1)))
        guide_changed = _guide_changed(user, guide_before)
        save_player(account, user)
    else:
        guide_changed = False
    response = _char_response(user, inst_id, char_fields=("skills",))
    if guide_changed:
        response["playerDataDelta"]["modified"]["mission"] = user.get("mission", {})
    return response


@router.post("/charBuild/addonStory/unlock")
async def char_build_addon_story():
    return {"result": 0, **empty_delta()}


@router.post("/charBuild/lockChar")
async def char_build_lock_char():
    return {"result": 0, **empty_delta()}


@router.post("/charBuild/sellChar")
async def char_build_sell_char():
    return {"result": 0, **empty_delta()}


@router.post("/building/sync")
async def building_sync(request: Request):
    """Answer the client's periodic building heartbeat with a real timestamp.

    The generic fallback returned only an empty result.  BuildingSyncResponse
    has a required ``ts`` field and the client retries about once per second
    when it is missing, flooding the UI thread (and making buttons such as the
    character-level upgrade button feel unresponsive).
    """
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    user = player_data(account)
    building = user.get("building") or {}
    modified = {}
    building_delta = {
        key: building.get(key)
        for key in ("rooms", "roomSlots", "status", "chars", "assist")
        if key in building
    }
    if building_delta:
        modified["building"] = building_delta
    event = user.get("event") or {}
    if isinstance(event, dict) and event.get("building") is not None:
        modified["event"] = {"building": event.get("building")}
    return {
        "result": 0,
        "ts": now_ts(),
        "playerDataDelta": {"deleted": {}, "modified": modified},
    }


@router.post("/gacha/getPoolDetail")
async def gacha_pool_detail(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    logger.info("gacha pool detail poolId=%r", body.get("poolId"))
    detail = gacha_service.pool_detail(body.get("poolId"))
    if not detail:
        return {"result": 1, "error": "pool not found"}
    return detail


def _gacha_response(account, user, result):
    if result.get("result") == 0:
        save_player(account, user)
    return result


@router.post("/gacha/advancedGacha")
async def gacha_advanced(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    result = gacha_service.draw(
        user,
        body.get("poolId"),
        count=1,
        use_tkt=body.get("useTkt"),
        item_id=body.get("itemId") or "",
    )
    logger.warning(
        "gacha single uid=%s body=%s result=%s error=%r",
        account["uid"], body, result.get("result"), result.get("error"),
    )
    return _gacha_response(account, user, result)


@router.post("/gacha/tenAdvancedGacha")
async def gacha_ten_advanced(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    item_list = body.get("itemList") or []
    item_id = ""
    if isinstance(item_list, list):
        for raw in item_list:
            if isinstance(raw, dict) and raw.get("id"):
                item_id = str(raw.get("id"))
                break
    result = gacha_service.draw(
        user,
        body.get("poolId"),
        count=10,
        use_tkt=body.get("useTkt"),
        item_id=item_id,
        item_list=item_list,
    )
    logger.warning(
        "gacha ten uid=%s body=%s itemId=%r result=%s error=%r",
        account["uid"], body, item_id, result.get("result"), result.get("error"),
    )
    return _gacha_response(account, user, result)


@router.post("/gacha/syncNormalGacha")
async def gacha_sync_normal(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    user = player_data(account)
    gacha_service.ensure_gacha_state(user)
    missions.ensure_mission_state(user)
    recruit = gacha_service.public_recruit_state(user)
    save_player(account, user)
    return {
        "result": 0,
        "recruit": recruit,
        "playerDataDelta": {"deleted": {}, "modified": {"recruit": recruit, "gacha": user.get("gacha", {})}},
    }


@router.post("/gacha/refreshTags")
async def gacha_refresh_tags(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    recruit = gacha_service.public_recruit_state(user)
    slot = recruit["normal"]["slots"].setdefault(str(body.get("slotId") or 0), {})
    tags = body.get("tags")
    if not isinstance(tags, list) or not tags:
        tags = sorted(random.sample(range(1, 30), 5))
    slot["tags"] = [int(_as_int(value, 0)) for value in tags if _as_int(value, 0)]
    slot.setdefault("selectTags", [])
    slot.setdefault("state", 0)
    save_player(account, user)
    return {"result": 0, "playerDataDelta": {"deleted": {}, "modified": {"recruit": recruit}}}


@router.post("/gacha/normalGacha")
async def gacha_normal(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    result = gacha_service.start_public_recruit(
        user,
        body.get("slotId"),
        body.get("tags") or body.get("selectTags") or [],
        body.get("durationInSec") or body.get("duration") or 3600,
    )
    if result.get("result") == 0:
        save_player(account, user)
    return result


@router.post("/gacha/finishNormalGacha")
async def gacha_finish_normal(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    recruit = gacha_service.public_recruit_state(user)
    slot_id = str(body.get("slotId") or 0)
    slot = recruit["normal"]["slots"].setdefault(slot_id, {})
    if _as_int(slot.get("state"), 0) != 1:
        return {"result": 1, "error": "recruit not finished"}
    char_id = random.choice(["char_123_fang", "char_122_beagle", "char_124_kroos", "char_121_lava"])
    grant_items, delta = grants.apply_items(
        user, [{"id": char_id, "type": "CHAR", "count": 1}], source="public-recruit"
    )
    slot.update({"state": 0, "startTs": -1, "realFinishTs": -1, "maxFinishTs": -1, "selectTags": [], "durationInSec": -1})
    mission_changed = bool(missions.record_mission_event(user, "NormalGacha"))
    save_player(account, user)
    response = {
        "result": 0,
        "charGet": {"charId": char_id, "itemGet": grant_items},
        "playerDataDelta": {
            "deleted": {},
            "modified": {**delta.get("modified", {}), "recruit": recruit},
        },
    }
    if mission_changed:
        response["playerDataDelta"]["modified"]["mission"] = user.get("mission", {})
    return response


@router.post("/gacha/cancelNormalGacha")
async def gacha_cancel_normal(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    recruit = gacha_service.public_recruit_state(user)
    slot = recruit["normal"]["slots"].setdefault(str(body.get("slotId") or 0), {})
    slot.update({"state": 0, "startTs": -1, "realFinishTs": -1, "maxFinishTs": -1, "selectTags": []})
    save_player(account, user)
    return {"result": 0, "playerDataDelta": {"deleted": {}, "modified": {"recruit": recruit}}}


@router.post("/gacha/boostNormalGacha")
async def gacha_boost_normal(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    user = player_data(account)
    recruit = gacha_service.public_recruit_state(user)
    slot = recruit["normal"]["slots"].setdefault(str(body.get("slotId") or 0), {})
    if _as_int(slot.get("state"), 0) != 1:
        return {"result": 1, "error": "recruit not running"}
    status = user.setdefault("status", {})
    tickets = _as_int(status.get("instantFinishTicket"), 0)
    if tickets <= 0:
        return {"result": 1, "error": "instant finish ticket not enough"}
    status["instantFinishTicket"] = tickets - 1
    slot["state"] = 2
    slot["realFinishTs"] = now_ts()
    save_player(account, user)
    return {"result": 0, "playerDataDelta": {"deleted": {}, "modified": {"status": user.get("status", {}), "recruit": recruit}}}


@router.post("/gacha/buyRecruitSlot")
async def gacha_buy_slot():
    return {"result": 0, **empty_delta()}


@router.post("/gacha/getFreeChar")
async def gacha_get_free_char():
    return {"result": 0, **empty_delta()}


@router.post("/gacha/choosePoolUp")
async def gacha_choose_pool_up(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    user = player_data(account)
    result = gacha_service.choose_pool_up(
        user,
        body.get("poolId"),
        body.get("chooseChar") or {},
    )
    if result.get("result") == 0:
        save_player(account, user)
    logger.info("gacha choosePoolUp uid=%s body=%s result=%s", account.get("uid"), body, result.get("result"))
    return result


async def _shop_buy(request, shop_key):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    user = player_data(account)
    result = shop_service.buy_shop_good(
        user,
        shop_key,
        body.get("goodId"),
        count=body.get("count") or body.get("buyCount") or 1,
        platform=_shop_platform(account, body),
    )
    if result.get("result") == 0:
        if str(shop_key or "").upper() == "SOCIAL":
            if missions.record_mission_event(user, "BuyShopItem"):
                result.setdefault("playerDataDelta", {}).setdefault("modified", {})[
                    "mission"
                ] = user.get("mission", {})
        save_player(account, user)
    return result


@router.post("/shop/buyLowGood")
async def buy_low_good(request: Request):
    return await _shop_buy(request, "LS")


@router.post("/shop/buyHighGood")
async def buy_high_good(request: Request):
    return await _shop_buy(request, "HS")


@router.post("/shop/buyExtraGood")
async def buy_extra_good(request: Request):
    return await _shop_buy(request, "ES")


@router.post("/shop/buyClassicGood")
async def buy_classic_good(request: Request):
    return await _shop_buy(request, "CLASSIC")


@router.post("/shop/buyEPGSGood")
async def buy_epgs_good(request: Request):
    return await _shop_buy(request, "EPGS")


@router.post("/shop/buySocialGood")
async def buy_social_good(request: Request):
    return await _shop_buy(request, "SOCIAL")


@router.post("/shop/buyRepGood")
async def buy_rep_good(request: Request):
    return await _shop_buy(request, "REP")


@router.post("/shop/buyLMTGSGood")
async def buy_lmtgs_good(request: Request):
    return await _shop_buy(request, "LMTGS")


@router.post("/shop/buySkinGood")
async def buy_skin_good(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    user = player_data(account)
    result = shop_service.buy_skin(user, body.get("goodId"), platform=_shop_platform(account, body))
    if result.get("result") == 0:
        save_player(account, user)
    return result


@router.post("/shop/buyFurniGood")
async def buy_furni_good(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    user = player_data(account)
    result = shop_service.buy_furni(
        user,
        body.get("goodId"),
        buy_count=body.get("buyCount") or 1,
        cost_type=body.get("costType") or "",
        platform=_shop_platform(account, body),
    )
    if result.get("result") == 0:
        save_player(account, user)
    return result


@router.post("/shop/buyFurniGroup")
async def buy_furni_group(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    user = player_data(account)
    result = shop_service.buy_furni_group(
        user,
        body.get("groupId"),
        body.get("goods") or [],
        cost_type=body.get("costType") or "",
        platform=_shop_platform(account, body),
    )
    if result.get("result") == 0:
        save_player(account, user)
    return result


@router.post("/shop/decomposePotentialItem")
async def decompose_potential_item(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    user = player_data(account)
    inventory = user.setdefault("inventory", {})
    status = user.setdefault("status", {})
    chars = (user.get("troop") or {}).get("chars") or {}
    items = []
    for raw in body.get("charInstIdList") or []:
        char = chars.get(str(raw))
        if not isinstance(char, dict):
            continue
        char_id = str(char.get("charId") or "")
        token_id = f"p_{char_id}"
        count = _as_int(inventory.get(token_id), 0)
        if count <= 0:
            continue
        inventory.pop(token_id, None)
        rarity = _as_int(str((game_data.character(char_id) or {}).get("rarity") or "TIER_1").replace("TIER_", ""), 1)
        if rarity >= 5:
            status["hggShard"] = _as_int(status.get("hggShard"), 0) + count
            items.append({"id": "4004", "type": "HGG_SHD", "count": count})
        else:
            status["lggShard"] = _as_int(status.get("lggShard"), 0) + count
            items.append({"id": "4005", "type": "LGG_SHD", "count": count})
    if items:
        save_player(account, user)
    return {
        "result": 0,
        "items": items,
        "playerDataDelta": {"deleted": {}, "modified": {"inventory": inventory, "status": status}},
    }


@router.post("/shop/checkForbidden")
async def shop_check_forbidden():
    return {"result": 0, "gacha": [], "shop": [], **empty_delta()}

