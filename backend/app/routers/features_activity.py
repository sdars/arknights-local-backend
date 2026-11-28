"""Activity protocol surface.

Side-story events are intentionally kept in one state tree
(``user.activity.runs``).  The tree stores the event-specific board, mission,
shop and battle records while every route below still has its own endpoint.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Request

from ..db import now_ts
from ..feature_common import (
    apply_items,
    as_dict,
    as_int,
    auth_error,
    battle_id,
    body_id,
    call,
    ensure_dict,
    ensure_list,
    fail,
    finish,
    first_value,
    unique_append,
)


router = APIRouter()


CATEGORY_MAP = {
    "act13side": "TYPE_ACT13SIDE",
    "act1dp": "TYPE_ACT1DP",
    "act1vhalfidle": "TYPE_ACT1VHALFIDLE",
    "act24side": "TYPE_ACT24SIDE",
    "act27side": "TYPE_ACT27SIDE",
    "act35side": "TYPE_ACT35SIDE",
    "act38side": "TYPE_ACT38SIDE",
    "act42d0": "TYPE_ACT42D0",
    "act42side": "TYPE_ACT42SIDE",
    "act44side": "TYPE_ACT44SIDE",
    "act45side": "TYPE_ACT45SIDE",
    "act46side": "TYPE_ACT46SIDE",
    "act53side": "TYPE_ACT53SIDE",
    "act54side": "TYPE_ACT54SIDE",
    "act55side": "TYPE_ACT55SIDE",
    "arcade": "ARCADE",
    "arkhub": "ARK_HUB",
    "bossRush": "TYPE_BOSSRUSH",
    "enemyDuel": "ENEMY_DUEL",
    "football": "ACT_FOOTBALL",
    "interlock": "TYPE_INTERLOCK",
    "multiplayerV3": "MULTIPLAY_V3",
    "typeAct20side": "TYPE_ACT20SIDE",
    "typeAct3d0": "TYPE_ACT3D0",
    "typeAct4d0": "TYPE_ACT4D0",
    "typeAct5d0": "TYPE_ACT5D0",
    "typeAct5d1": "TYPE_ACT5D1",
    "typeAct9d0": "TYPE_ACT9D0",
    "vecBreakV2": "VEC_BREAK_V2",
    "year5General": "TYPE_YEAR5GENERAL",
}


STATE_KEY_DEFAULTS = {
    "arcade": "act1museum",
    "vecBreakV2": "act3break",
    "typeAct20side": "act20side",
    "typeAct3d0": "act3d0",
    "typeAct4d0": "act4d0",
    "typeAct5d0": "act5d0",
    "typeAct5d1": "act5d1",
    "typeAct9d0": "act9d0",
}


def activity_route(path: str):
    def decorator(fn: Callable):
        router.add_api_route(path, fn, methods=["POST"], name=fn.__name__)
        return fn

    return decorator


def _run(call_obj, activity_id: str) -> dict:
    root = ensure_dict(call_obj.user, "activity")
    category = CATEGORY_MAP.get(activity_id)
    if category:
        bucket = ensure_dict(root, category)
        state_key = body_id(call_obj.body, "activityId", "activity", default=STATE_KEY_DEFAULTS.get(activity_id, activity_id))
        run = bucket.get(state_key)
        if not isinstance(run, dict):
            run = {}
            bucket[state_key] = run
    else:
        state_key = activity_id
        bucket = None
        run = None
    if run is None:
        runs = ensure_dict(root, "runs")
        run = runs.get(activity_id)
        if not isinstance(run, dict):
            run = {}
            runs[activity_id] = run
    run.setdefault("state", "IDLE")
    run.setdefault("missions", {})
    run.setdefault("claimed", {})
    run.setdefault("items", {})
    run.setdefault("shop", {})
    run.setdefault("board", {})
    run.setdefault("squad", [])
    run.setdefault("battle", {})
    run.setdefault("team", {})
    run.setdefault("match", {})
    run.setdefault("flags", {})
    return run


def _grant(call_obj, amount: int = 500, suffix: str = "activity") -> list[dict]:
    return apply_items(call_obj, [{"id": "4001", "type": "GOLD", "count": max(1, amount)}], suffix)


def _parts(path: str) -> tuple[str, str]:
    text = path.strip("/").split("/")
    if len(text) >= 3 and text[0] == "activity":
        return text[1], text[-1]
    return "global", text[-1] if text else "index"


def _battle_start(call_obj, run: dict, prefix: str = "activity") -> dict:
    run["state"] = "BATTLE"
    run["battle"]["id"] = battle_id(prefix, call_obj.account)
    run["battle"]["stage"] = body_id(call_obj.body, "stageId", "nodeId", "id")
    run["battle"]["startTs"] = now_ts()
    call_obj.extra["battleId"] = run["battle"]["id"]
    return finish(call_obj, "activity")


def _battle_finish(call_obj, run: dict) -> dict:
    run["state"] = "IDLE"
    run["battle"]["result"] = "WIN" if as_int(call_obj.body.get("completeState"), 3) >= 2 else "LOSE"
    run["battle"]["finishTs"] = now_ts()
    run["flags"]["battleCleared"] = 1
    run["score"] = max(as_int(run.get("score"), 0), as_int(call_obj.body.get("score"), 0))
    call_obj.extra["items"] = []
    return finish(call_obj, "activity")


def _team(call_obj, run: dict, action: str) -> dict:
    team = ensure_dict(run, "team")
    if action in {"createTeam", "create"}:
        team.update(
            {
                "teamId": body_id(call_obj.body, "teamId", "id", default=f"team_{call_obj.account['uid']}"),
                "leaderUid": str(call_obj.account["uid"]),
                "members": [str(call_obj.account["uid"])],
                "createdTs": now_ts(),
            }
        )
    elif action == "joinTeam":
        team.setdefault("teamId", body_id(call_obj.body, "teamId", "id"))
        unique_append(ensure_list(team, "members"), str(call_obj.account["uid"]))
    elif action in {"queryMatch", "startMatch"}:
        match = ensure_dict(run, "match")
        match["state"] = "MATCHING" if action == "queryMatch" else "BATTLE"
        match["queryTs"] = now_ts()
    elif action in {"multiBattleStart", "singleBattleStart", "guideBattleStart"}:
        run["state"] = "BATTLE"
        run["battle"]["id"] = battle_id("activity-pvp", call_obj.account)
        call_obj.extra["battleId"] = run["battle"]["id"]
    elif action in {"multiBattleFinish", "singleBattleFinish", "guideBattleFinish"}:
        run["state"] = "IDLE"
        run["match"]["finishedTs"] = now_ts()
        run["match"]["win"] = bool(as_int(call_obj.body.get("win"), 1))
    return finish(call_obj, "activity")


def _shop(call_obj, run: dict, path: str) -> dict:
    shop = ensure_dict(run, "shop")
    if path.endswith("getActivityShopInfo") or path.endswith("getGoodsList") or path.endswith("getGoodsInfo"):
        return {
            "result": 0,
            "shop": shop.get("goods") or [],
            "goodsList": shop.get("goods") or [],
            "playerDataDelta": {"deleted": {}, "modified": {"activity": call_obj.user["activity"]}},
        }
    good_id = body_id(call_obj.body, "goodId", "itemId", "id")
    count = max(1, as_int(call_obj.body.get("count"), 1))
    goods = ensure_dict(shop, "goods")
    good = ensure_dict(goods, good_id)
    good["bought"] = as_int(good.get("bought"), 0) + count
    items = ensure_dict(run, "items")
    items[good_id] = as_int(items.get(good_id), 0) + count
    call_obj.extra["items"] = [{"id": good_id, "type": "MATERIAL", "count": count}]
    return finish(call_obj, "activity")


def _missions(call_obj, run: dict, action: str) -> dict:
    missions = ensure_dict(run, "missions")
    mission_id = body_id(call_obj.body, "missionId", "id")
    if action in {"confirmActivityMission", "confirmActivityMissionGroup", "confirmActivityMissionList", "confirmTask", "milestone", "milestoneBatch"}:
        ids = (
            call_obj.body.get("missionIds")
            or call_obj.body.get("missionList")
            or call_obj.body.get("groupIds")
            or call_obj.body.get("ids")
            or [mission_id]
        )
        if not isinstance(ids, list):
            ids = [ids]
        claimed = ensure_dict(run, "claimed")
        gained = []
        for raw in ids:
            mid = str(raw or "")
            if not mid or claimed.get(mid):
                continue
            claimed[mid] = 1
            ensure_dict(missions, mid)["state"] = 2
            gained.extend(_grant(call_obj, 500, f"activity-mission:{mid}"))
        call_obj.extra["items"] = gained
    elif action in {"acceptTask", "acceptTask"}:
        ensure_dict(missions, mission_id or "task")["state"] = 1
    elif action.startswith("dailyMission"):
        daily = ensure_dict(run, "dailyMissions")
        if action in {"dailyMissionAccept", "dailyMissionRandom"}:
            daily[mission_id or "1"] = {"state": 1, "ts": now_ts()}
        elif action in {"dailyMissionCancel", "dailyMissionReplace"}:
            daily.pop(mission_id, None)
        elif action == "dailyMissionCommit":
            daily[mission_id or "1"] = {"state": 2, "ts": now_ts()}
            call_obj.extra["items"] = _grant(call_obj, 300, "activity-daily")
    elif action.startswith("longMission"):
        long_missions = ensure_dict(run, "longMissions")
        ids = call_obj.body.get("missionIds") if action.endswith("Batch") else [mission_id]
        if not isinstance(ids, list):
            ids = [ids]
        for mid in ids:
            if mid:
                long_missions[str(mid)] = {"state": 2, "ts": now_ts()}
        call_obj.extra["items"] = _grant(call_obj, 300 * len(ids), "activity-long")
    return finish(call_obj, "activity")


def _activity_action(call_obj, path: str) -> dict:
    activity_id, action = _parts(path)
    run = _run(call_obj, activity_id)
    if action in {"battleStart", "challengeStart", "defendBattleStart", "guideBattleStart"}:
        return _battle_start(call_obj, run, activity_id)
    if action in {"battleFinish", "challengeFinish", "defendBattleFinish", "guideBattleFinish"}:
        return _battle_finish(call_obj, run)
    if action in {"createTeam", "joinTeam", "queryMatch", "startMatch", "multiBattleStart", "multiBattleFinish", "singleBattleStart", "singleBattleFinish", "guideBattleStart", "guideBattleFinish"}:
        return _team(call_obj, run, action)
    if action in {"getActivityShopInfo", "exchangeActivityShopItem", "getGoodsList", "buyGoods", "buyRune"}:
        return _shop(call_obj, run, path)
    if action in {
        "confirmActivityMission",
        "confirmActivityMissionGroup",
        "confirmActivityMissionList",
        "acceptTask",
        "confirmTask",
        "dailyMissionAccept",
        "dailyMissionCancel",
        "dailyMissionCommit",
        "dailyMissionRandom",
        "dailyMissionReplace",
        "longMissionCommit",
        "longMissionCommitBatch",
        "milestone",
        "milestoneBatch",
    }:
        return _missions(call_obj, run, action)
    if action == "getActivityCheckInReward":
        index = as_int(first_value(call_obj.body, "index", "day", default=0), 0)
        checked = ensure_dict(run, "checkIn")
        if checked.get(str(index)):
            return fail("check-in reward already claimed")
        checked[str(index)] = 1
        call_obj.extra["items"] = _grant(call_obj, 300, "activity-checkin")
        return finish(call_obj, "activity")
    if action == "getActivityCollectionReward":
        index = as_int(first_value(call_obj.body, "index", "id", default=0), 0)
        collection = ensure_dict(run, "collection")
        if collection.get(str(index)):
            return fail("collection reward already claimed")
        collection[str(index)] = 1
        call_obj.extra["items"] = _grant(call_obj, 500, "activity-collection")
        return finish(call_obj, "activity")
    if action == "clearFlag":
        flag = body_id(call_obj.body, "flag", "id", default="cleared")
        run["flags"][flag] = 1
        return finish(call_obj, "activity")
    if action == "getInfo" or action == "syncInfo" or action == "getGachaInfo":
        return {
            "result": 0,
            "info": run,
            "state": run.get("state"),
            "playerDataDelta": {"deleted": {}, "modified": {"activity": call_obj.user["activity"]}},
        }
    if action == "getDailyRecruitList" or action == "getFriendUidList":
        return {"result": 0, "uidList": [], "recruitList": [], "playerDataDelta": {"deleted": {}, "modified": {"activity": call_obj.user["activity"]}}}
    if action == "getFriendUidList":
        return {"result": 0, "uidList": [], "playerDataDelta": {"deleted": {}, "modified": {"activity": call_obj.user["activity"]}}}
    if action == "getPixelArt":
        return {"result": 0, "pixelArt": run.get("pixelArt") or {}, "playerDataDelta": {"deleted": {}, "modified": {"activity": call_obj.user["activity"]}}}
    if action == "savePixelArt":
        run["pixelArt"] = call_obj.body.get("pixelArt") or call_obj.body.get("data") or {}
        return finish(call_obj, "activity")
    if action == "enterHall":
        run["hallEnteredTs"] = now_ts()
        return finish(call_obj, "activity")
    if action == "report":
        ensure_dict(run, "report").update({key: value for key, value in call_obj.body.items() if key not in {"secret", "token"}})
        return finish(call_obj, "activity")
    if action in {"setSecretary", "setSquad", "setSquads", "setBuff", "setDefend", "changePhoto", "changeTitle", "commitAlbum"}:
        key = action.removeprefix("set").removeprefix("change").lower()
        run[key] = call_obj.body.get(key) or call_obj.body.get("data") or call_obj.body.get("squad") or call_obj.body.get("slots") or []
        return finish(call_obj, "activity")
    if action in {"savePixelArt", "setSecretary", "setSquad"}:
        return finish(call_obj, "activity")
    if action in {"rewardMilestone", "rewardAllMilestone", "quickGetMilestoneAward", "recvMilestone"}:
        claimed = ensure_dict(run, "milestones")
        ids = call_obj.body.get("indexList") or call_obj.body.get("ids") or call_obj.body.get("milestoneIds") or [body_id(call_obj.body, "index", "id", default="0")]
        if not isinstance(ids, list):
            ids = [ids]
        gained = []
        for raw in ids:
            key = str(raw)
            if key and not claimed.get(key):
                claimed[key] = 1
                gained.extend(_grant(call_obj, 500, "activity-milestone"))
        call_obj.extra["items"] = gained
        return finish(call_obj, "activity")
    if action in {"recycleCharms", "typeAct20side/quickRecycle", "tryGetCharmFirstReward"}:
        charm = call_obj.body.get("charmId") or call_obj.body.get("id") or "charm"
        ensure_dict(run, "recycled")[str(charm)] = 1
        call_obj.extra["items"] = _grant(call_obj, 250, "activity-charm")
        return finish(call_obj, "activity")
    if activity_id == "roguelike":
        return _roguelike(call_obj, run, "roguelike/" + action)
    if action.startswith("typeAct20side/") or activity_id == "typeAct20side":
        return _act20(call_obj, run, action)
    if activity_id == "typeAct3d0":
        return _act3d0(call_obj, run, action)
    if activity_id in {"typeAct4d0", "typeAct5d0", "typeAct5d1", "typeAct9d0"}:
        return _legacy_activity(call_obj, run, activity_id, action)
    if activity_id == "act13side":
        if action.startswith("dailyMission") or action.startswith("longMission") or action == "clearFlag":
            if action == "clearFlag":
                run["flags"][body_id(call_obj.body, "flag", "id", default="cleared")] = 1
            return finish(call_obj, "activity")
    if activity_id == "act1vhalfidle":
        return _half_idle(call_obj, run, action)
    if activity_id == "act24side":
        return _act24(call_obj, run, action)
    if activity_id == "act27side":
        return _act27(call_obj, run, action)
    if activity_id == "act35side":
        return _act35(call_obj, run, action)
    if activity_id == "act38side":
        return _act38(call_obj, run, action)
    if activity_id == "act42d0":
        return _act42d0(call_obj, run, action)
    if activity_id == "act42side":
        return _act42side(call_obj, run, action)
    if activity_id == "act44side":
        return _act44(call_obj, run, action)
    if activity_id == "act45side":
        run[action] = True
        return finish(call_obj, "activity")
    if activity_id == "act46side":
        return _act46(call_obj, run, action)
    if activity_id in {"act54side", "act55side"}:
        if action == "getFinalReward":
            ensure_dict(run, "claimed")["final"] = 1
            call_obj.extra["items"] = _grant(call_obj, 800, "activity-final")
        else:
            run[action] = True
        return finish(call_obj, "activity")
    if activity_id == "arkhub":
        return _arkhub(call_obj, run, action)
    if activity_id == "bossRush":
        return _boss_rush(call_obj, run, action)
    if activity_id == "interlock":
        return _interlock(call_obj, run, action)
    if activity_id == "multiplayerV3":
        return _multiplayer(call_obj, run, action)
    if activity_id == "vecBreakV2":
        return _vec_break(call_obj, run, action)
    if activity_id == "year5General":
        if action == "getInfReward":
            ensure_dict(run, "claimed")["inf"] = 1
            call_obj.extra["items"] = _grant(call_obj, 800, "activity-year5")
        return finish(call_obj, "activity")
    if activity_id in {"arcade", "football"}:
        if action == "battleStart":
            return _battle_start(call_obj, run, activity_id)
        if action == "battleFinish":
            return _battle_finish(call_obj, run)
    if action in {"startGame", "create", "createGame"}:
        run["state"] = "RUNNING"
        run["startTs"] = now_ts()
        return finish(call_obj, "activity")
    if action in {"nextState", "startGame", "nextRound", "nextDay", "process", "toProcess", "toBuy", "move", "mining", "endRound", "settleGame", "settle", "reading", "useInsight", "selectChoice", "choose"}:
        run[action] = {"body": call_obj.body, "ts": now_ts()}
        if action in {"settleGame", "settle"}:
            run["state"] = "SETTLED"
            call_obj.extra["items"] = _grant(call_obj, 600, "activity-settle")
        elif action in {"nextDay", "nextRound"}:
            run["round"] = as_int(run.get("round"), 1) + 1
        return finish(call_obj, "activity")
    if action in {"buyCard", "buySlot", "refreshShop", "useHint", "completePuzzle", "setTool", "eat", "harvest", "upgradeChar", "upgradeSkill", "evolveChar", "unlockTech", "refreshProduct", "replaceRate", "setAssistChar", "recruitNormal", "recruitDirect", "inquirePurchase", "inquireSell", "purchase", "sell", "saleStart", "saleSettle", "selectFaction", "gacha", "readNews", "unlockStory", "finishStory", "getReward", "getDailyRewards", "getDailyTrustedItem", "confirmChar", "confirmMail", "relicSelect", "relicUpgrade", "changeBuffList", "getMilestoneReward", "getInfReward"}:
        run[action] = {"body": call_obj.body, "ts": now_ts()}
        return finish(call_obj, "activity")
    return fail(f"unsupported activity action: {path}")


def _roguelike(call_obj, run, action):
    sub = action.split("/", 1)[1]
    if sub == "createGame":
        run["state"] = "RUNNING"
        run["startTs"] = now_ts()
        run["seed"] = body_id(call_obj.body, "seed", default=str(now_ts()))
    elif sub in {"finishGame", "giveUpGame"}:
        run["state"] = "FINISHED"
        run["finishTs"] = now_ts()
    elif sub in {"milestoneReward", "milestoneRewardTryBest"}:
        ensure_dict(run, "claimed")[body_id(call_obj.body, "id", "index", default="0")] = 1
        call_obj.extra["items"] = _grant(call_obj, 400, "activity-roguelike")
    elif sub == "upgradeOutBuff":
        buff = body_id(call_obj.body, "buff", "id")
        ensure_dict(run, "buff")[buff] = 1
    return finish(call_obj, "activity")


def _act20(call_obj, run, action):
    sub = action.split("/", 1)[-1]
    if sub in {"competitionStart"}:
        run["competition"] = {"state": "RUNNING", "startTs": now_ts()}
    elif sub in {"competitionFinish", "judge"}:
        run["competition"] = {"state": "FINISHED", "score": as_int(call_obj.body.get("score"), 0), "finishTs": now_ts()}
    elif sub in {"pick", "confirmExhiCar"}:
        run[sub] = call_obj.body.get("carId") or call_obj.body.get("id") or call_obj.body.get("choice")
    elif sub == "quickGetMilestoneAward":
        ensure_dict(run, "milestones")[body_id(call_obj.body, "id", "index", default="0")] = 1
        call_obj.extra["items"] = _grant(call_obj, 300, "activity-act20")
    else:
        run[sub] = True
    return finish(call_obj, "activity")


def _act3d0(call_obj, run, action):
    if action == "gacha":
        count = max(1, as_int(call_obj.body.get("count"), 1))
        run["gachaCount"] = as_int(run.get("gachaCount"), 0) + count
        run["gachaResult"] = _grant(call_obj, 100 * count, "activity-type3d0")
        call_obj.extra["items"] = run["gachaResult"]
    elif action == "getGachaInfo":
        return {"result": 0, "info": run, "playerDataDelta": {"deleted": {}, "modified": {"activity": call_obj.user["activity"]}}}
    elif action == "getMilestoneReward":
        ensure_dict(run, "claimed")[body_id(call_obj.body, "id", "index", default="0")] = 1
        call_obj.extra["items"] = _grant(call_obj, 400, "activity-type3d0")
    else:
        run["faction"] = body_id(call_obj.body, "faction", "id")
    return finish(call_obj, "activity")


def _legacy_activity(call_obj, run, activity_id, action):
    if action == "getReward" or action == "getInfo":
        if action == "getReward":
            ensure_dict(run, "claimed")[body_id(call_obj.body, "id", "index", default="0")] = 1
            call_obj.extra["items"] = _grant(call_obj, 300, activity_id)
        else:
            return {"result": 0, "info": run, "playerDataDelta": {"deleted": {}, "modified": {"activity": call_obj.user["activity"]}}}
    elif action == "getGoodsList":
        return {"result": 0, "goodsList": run["shop"].get("goods") or [], "playerDataDelta": {"deleted": {}, "modified": {"activity": call_obj.user["activity"]}}}
    else:
        run[action] = call_obj.body
    return finish(call_obj, "activity")


def _half_idle(call_obj, run, action):
    if action == "harvest":
        run["harvestCount"] = as_int(run.get("harvestCount"), 0) + 1
        call_obj.extra["items"] = _grant(call_obj, 200, "activity-halfidle")
    elif action in {"recruitNormal", "recruitDirect"}:
        unique_append(ensure_list(run, "recruits"), body_id(call_obj.body, "charId", "id"))
    elif action in {"upgradeChar", "upgradeSkill", "evolveChar"}:
        char_id = body_id(call_obj.body, "charId", "id")
        ensure_dict(run, "chars")[char_id] = {"action": action, "ts": now_ts()}
    elif action == "unlockTech":
        unique_append(ensure_list(run, "tech"), body_id(call_obj.body, "techId", "id"))
    elif action == "refreshProduct":
        run["productRefresh"] = as_int(run.get("productRefresh"), 0) + 1
    elif action == "replaceRate":
        run["rates"] = call_obj.body.get("rates") or call_obj.body
    elif action == "setAssistChar":
        run["assistChar"] = body_id(call_obj.body, "charId", "id")
    else:
        run[action] = call_obj.body
    return finish(call_obj, "activity")


def _act24(call_obj, run, action):
    if action in {"alchemy", "eat", "setTool"}:
        run[action] = call_obj.body
        if action == "alchemy":
            call_obj.extra["items"] = _grant(call_obj, 150, "activity-act24")
    elif action == "getHuntCollectRewards":
        ensure_dict(run, "claimed")["hunt"] = 1
        call_obj.extra["items"] = _grant(call_obj, 500, "activity-act24")
    elif action in {"battleStart", "battleFinish"}:
        return _battle_start(call_obj, run, "act24") if action == "battleStart" else _battle_finish(call_obj, run)
    return finish(call_obj, "activity")


def _act27(call_obj, run, action):
    market = ensure_dict(run, "market")
    if action in {"inquirePurchase", "inquireSell"}:
        market[action] = {"id": body_id(call_obj.body, "id", "itemId"), "price": as_int(call_obj.body.get("price"), 0), "ts": now_ts()}
    elif action == "nextDay":
        run["day"] = as_int(run.get("day"), 0) + 1
    elif action in {"purchase", "sell"}:
        item_id = body_id(call_obj.body, "id", "itemId")
        count = max(1, as_int(call_obj.body.get("count"), 1))
        if action == "purchase":
            ensure_dict(run, "items")[item_id] = as_int(ensure_dict(run, "items").get(item_id), 0) + count
        else:
            ensure_dict(run, "sold")[item_id] = as_int(ensure_dict(run, "sold").get(item_id), 0) + count
    elif action in {"saleStart", "saleSettle"}:
        market[action] = {"body": call_obj.body, "ts": now_ts()}
    return finish(call_obj, "activity")


def _act35(call_obj, run, action):
    board = ensure_dict(run, "board")
    if action == "create":
        run["state"] = "RUNNING"
        board.clear()
        board["round"] = 1
    elif action in {"buyCard", "buySlot"}:
        key = body_id(call_obj.body, "cardId", "slotId", "id")
        ensure_dict(board, action)[key] = 1
    elif action == "refreshShop":
        board["shopRefresh"] = as_int(board.get("shopRefresh"), 0) + 1
    elif action in {"process", "toProcess", "toBuy", "nextRound"}:
        board[action] = call_obj.body
        if action == "nextRound":
            board["round"] = as_int(board.get("round"), 1) + 1
    elif action == "settle":
        run["state"] = "SETTLED"
        call_obj.extra["items"] = _grant(call_obj, 500, "activity-act35")
    return finish(call_obj, "activity")


def _act38(call_obj, run, action):
    puzzle = ensure_dict(run, "puzzle")
    if action == "getInfo":
        return {"result": 0, "info": puzzle, "playerDataDelta": {"deleted": {}, "modified": {"activity": call_obj.user["activity"]}}}
    if action == "useHint":
        puzzle["hint"] = call_obj.body.get("solutionList") or call_obj.body.get("hint") or []
    elif action == "completePuzzle":
        puzzle_id = body_id(call_obj.body, "puzzleId", "id")
        ensure_dict(puzzle, "completed")[puzzle_id] = 1
        call_obj.extra["items"] = _grant(call_obj, 300, "activity-act38")
    return finish(call_obj, "activity")


def _act42d0(call_obj, run, action):
    if action in {"battleStart", "challengeStart"}:
        return _battle_start(call_obj, run, "act42d0")
    if action in {"battleFinish", "challengeFinish"}:
        return _battle_finish(call_obj, run)
    if action == "recvMilestone":
        ensure_dict(run, "milestone")[body_id(call_obj.body, "id", "index", default="0")] = 1
        call_obj.extra["items"] = _grant(call_obj, 500, "activity-act42d0")
    return finish(call_obj, "activity")


def _act42side(call_obj, run, action):
    if action in {"acceptTask", "confirmTask"}:
        task_id = body_id(call_obj.body, "taskId", "id")
        ensure_dict(run, "tasks")[task_id] = {"state": 1 if action == "acceptTask" else 2, "ts": now_ts()}
    elif action == "getDailyRewards":
        ensure_dict(run, "claimed")["daily"] = 1
        call_obj.extra["items"] = _grant(call_obj, 300, "activity-act42side")
    elif action == "getDailyTrustedItem":
        ensure_dict(run, "claimed")["trusted"] = 1
        call_obj.extra["items"] = [{"id": "4006", "type": "MATERIAL", "count": 1}]
    return finish(call_obj, "activity")


def _act44(call_obj, run, action):
    if action == "startGame":
        run["state"] = "RUNNING"
        run["startTs"] = now_ts()
    elif action == "nextState":
        run["state"] = body_id(call_obj.body, "state", "nextState", default="RUNNING")
    elif action in {"selectChoice", "useInsight"}:
        run[action] = call_obj.body
    return finish(call_obj, "activity")


def _act46(call_obj, run, action):
    if action == "startGame":
        run["state"] = "RUNNING"
        run["position"] = {"x": 0, "y": 0}
    elif action == "move":
        run["position"] = {"x": as_int(call_obj.body.get("x"), 0), "y": as_int(call_obj.body.get("y"), 0)}
    elif action == "mining":
        run["mined"] = as_int(run.get("mined"), 0) + 1
        call_obj.extra["items"] = _grant(call_obj, 100, "activity-act46")
    elif action == "endRound":
        run["round"] = as_int(run.get("round"), 0) + 1
    elif action == "settleGame":
        run["state"] = "SETTLED"
        call_obj.extra["items"] = _grant(call_obj, 400, "activity-act46")
    return finish(call_obj, "activity")


def _arkhub(call_obj, run, action):
    if action == "enterHall":
        run["hallEntered"] = True
    elif action == "getFriendUidList":
        return {"result": 0, "uidList": [], "playerDataDelta": {"deleted": {}, "modified": {"activity": call_obj.user["activity"]}}}
    elif action == "getPixelArt":
        return {"result": 0, "pixelArt": run.get("pixelArt") or {}, "playerDataDelta": {"deleted": {}, "modified": {"activity": call_obj.user["activity"]}}}
    elif action == "savePixelArt":
        run["pixelArt"] = call_obj.body.get("pixelArt") or call_obj.body.get("data") or {}
    elif action == "report":
        ensure_dict(run, "report").update(call_obj.body)
    elif action in {"setSecretary", "setSquad"}:
        run[action] = call_obj.body
    elif action == "syncInfo":
        return {"result": 0, "info": run, "playerDataDelta": {"deleted": {}, "modified": {"activity": call_obj.user["activity"]}}}
    return finish(call_obj, "activity")


def _boss_rush(call_obj, run, action):
    if action == "battleStart":
        return _battle_start(call_obj, run, "bossRush")
    if action == "battleFinish":
        return _battle_finish(call_obj, run)
    if action in {"relicSelect", "relicUpgrade"}:
        relic_id = body_id(call_obj.body, "relicId", "id")
        ensure_dict(run, "relics")[relic_id] = {"action": action, "ts": now_ts()}
    return finish(call_obj, "activity")


def _interlock(call_obj, run, action):
    if action in {"milestone", "milestoneBatch"}:
        ids = call_obj.body.get("ids") or call_obj.body.get("milestoneIds") or [body_id(call_obj.body, "id", "index", default="0")]
        if not isinstance(ids, list):
            ids = [ids]
        claimed = ensure_dict(run, "milestones")
        gained = []
        for raw in ids:
            key = str(raw)
            if key and not claimed.get(key):
                claimed[key] = 1
                gained.extend(_grant(call_obj, 400, "activity-interlock"))
        call_obj.extra["items"] = gained
    elif action == "setDefend":
        run["defend"] = call_obj.body
    elif action == "setSquad":
        run["squad"] = call_obj.body.get("squad") or call_obj.body.get("slots") or []
    return finish(call_obj, "activity")


def _multiplayer(call_obj, run, action):
    if action in {"createTeam", "joinTeam", "startMatch", "queryMatch", "battleStart", "guideBattleStart", "battleFinish", "guideBattleFinish"}:
        return _team(call_obj, run, action)
    if action == "getInfo":
        return {"result": 0, "info": run, "playerDataDelta": {"deleted": {}, "modified": {"activity": call_obj.user["activity"]}}}
    if action in {"changePhoto", "changeTitle", "commitAlbum", "setBuff", "setSquads", "settleLike", "unlockBuff", "report"}:
        run[action] = call_obj.body
    return finish(call_obj, "activity")


def _vec_break(call_obj, run, action):
    if action in {"battleStart", "defendBattleStart"}:
        return _battle_start(call_obj, run, "vecBreak")
    if action in {"battleFinish", "defendBattleFinish"}:
        return _battle_finish(call_obj, run)
    if action == "setDefend":
        run["defendStages"] = call_obj.body
    elif action == "changeBuffList":
        run["buffList"] = call_obj.body.get("buffList") or call_obj.body.get("buff") or []
    return finish(call_obj, "activity")


# All activity routes in protocol_catalog.json are listed here.  The table is
# deliberately static so adding a catalog entry does not silently create a
# wildcard endpoint.
ACTIVITY_PATHS = [
    '/activity/act13side/clearFlag',
    '/activity/act13side/dailyMissionAccept',
    '/activity/act13side/dailyMissionCancel',
    '/activity/act13side/dailyMissionCommit',
    '/activity/act13side/dailyMissionRandom',
    '/activity/act13side/dailyMissionReplace',
    '/activity/act13side/longMissionCommit',
    '/activity/act13side/longMissionCommitBatch',
    '/activity/act1dp/battleFinish',
    '/activity/act1dp/battleStart',
    '/activity/act1vhalfidle/battleFinish',
    '/activity/act1vhalfidle/battleStart',
    '/activity/act1vhalfidle/evolveChar',
    '/activity/act1vhalfidle/harvest',
    '/activity/act1vhalfidle/recruitDirect',
    '/activity/act1vhalfidle/recruitNormal',
    '/activity/act1vhalfidle/refreshProduct',
    '/activity/act1vhalfidle/replaceRate',
    '/activity/act1vhalfidle/setAssistChar',
    '/activity/act1vhalfidle/unlockTech',
    '/activity/act1vhalfidle/upgradeChar',
    '/activity/act1vhalfidle/upgradeSkill',
    '/activity/act24side/alchemy',
    '/activity/act24side/battleFinish',
    '/activity/act24side/battleStart',
    '/activity/act24side/eat',
    '/activity/act24side/getHuntCollectRewards',
    '/activity/act24side/setTool',
    '/activity/act27side/inquirePurchase',
    '/activity/act27side/inquireSell',
    '/activity/act27side/nextDay',
    '/activity/act27side/purchase',
    '/activity/act27side/saleSettle',
    '/activity/act27side/saleStart',
    '/activity/act27side/sell',
    '/activity/act35side/buyCard',
    '/activity/act35side/buySlot',
    '/activity/act35side/create',
    '/activity/act35side/nextRound',
    '/activity/act35side/process',
    '/activity/act35side/refreshShop',
    '/activity/act35side/settle',
    '/activity/act35side/toBuy',
    '/activity/act35side/toProcess',
    '/activity/act38side/completePuzzle',
    '/activity/act38side/getInfo',
    '/activity/act38side/useHint',
    '/activity/act42d0/battleFinish',
    '/activity/act42d0/battleStart',
    '/activity/act42d0/challengeFinish',
    '/activity/act42d0/challengeStart',
    '/activity/act42d0/recvMilestone',
    '/activity/act42side/acceptTask',
    '/activity/act42side/confirmTask',
    '/activity/act42side/getDailyRewards',
    '/activity/act42side/getDailyTrustedItem',
    '/activity/act44side/nextState',
    '/activity/act44side/selectChoice',
    '/activity/act44side/startGame',
    '/activity/act44side/useInsight',
    '/activity/act45side/confirmChar',
    '/activity/act45side/confirmMail',
    '/activity/act46side/endRound',
    '/activity/act46side/mining',
    '/activity/act46side/move',
    '/activity/act46side/settleGame',
    '/activity/act46side/startGame',
    '/activity/act54side/getFinalReward',
    '/activity/act54side/reading',
    '/activity/act55side/settleGame',
    '/activity/arcade/battleFinish',
    '/activity/arcade/battleStart',
    '/activity/arkhub/enterHall',
    '/activity/arkhub/getFriendUidList',
    '/activity/arkhub/getPixelArt',
    '/activity/arkhub/report',
    '/activity/arkhub/savePixelArt',
    '/activity/arkhub/setSecretary',
    '/activity/arkhub/setSquad',
    '/activity/arkhub/syncInfo',
    '/activity/bossRush/battleFinish',
    '/activity/bossRush/battleStart',
    '/activity/bossRush/relicSelect',
    '/activity/bossRush/relicUpgrade',
    '/activity/confirmActivityMission',
    '/activity/confirmActivityMissionGroup',
    '/activity/confirmActivityMissionList',
    '/activity/enemyDuel/createTeam',
    '/activity/enemyDuel/joinTeam',
    '/activity/enemyDuel/multiBattleFinish',
    '/activity/enemyDuel/multiBattleStart',
    '/activity/enemyDuel/queryMatch',
    '/activity/enemyDuel/singleBattleFinish',
    '/activity/enemyDuel/singleBattleStart',
    '/activity/enemyDuel/startMatch',
    '/activity/exchangeActivityShopItem',
    '/activity/football/battleFinish',
    '/activity/football/battleStart',
    '/activity/getActivityCheckInReward',
    '/activity/getActivityCollectionReward',
    '/activity/getActivityShopInfo',
    '/activity/interlock/milestone',
    '/activity/interlock/milestoneBatch',
    '/activity/interlock/setDefend',
    '/activity/interlock/setSquad',
    '/activity/multiplayerV3/battleFinish',
    '/activity/multiplayerV3/battleStart',
    '/activity/multiplayerV3/changePhoto',
    '/activity/multiplayerV3/changeTitle',
    '/activity/multiplayerV3/commitAlbum',
    '/activity/multiplayerV3/createTeam',
    '/activity/multiplayerV3/getInfo',
    '/activity/multiplayerV3/guideBattleFinish',
    '/activity/multiplayerV3/guideBattleStart',
    '/activity/multiplayerV3/joinTeam',
    '/activity/multiplayerV3/queryMatch',
    '/activity/multiplayerV3/report',
    '/activity/multiplayerV3/setBuff',
    '/activity/multiplayerV3/setSquads',
    '/activity/multiplayerV3/settleLike',
    '/activity/multiplayerV3/startMatch',
    '/activity/multiplayerV3/unlockBuff',
    '/activity/recycleCharms',
    '/activity/rewardAllMilestone',
    '/activity/rewardMilestone',
    '/activity/roguelike/createGame',
    '/activity/roguelike/finishGame',
    '/activity/roguelike/giveUpGame',
    '/activity/roguelike/milestoneReward',
    '/activity/roguelike/milestoneRewardTryBest',
    '/activity/roguelike/upgradeOutBuff',
    '/activity/tryGetCharmFirstReward',
    '/activity/typeAct20side/competitionFinish',
    '/activity/typeAct20side/competitionStart',
    '/activity/typeAct20side/confirmExhiCar',
    '/activity/typeAct20side/judge',
    '/activity/typeAct20side/pick',
    '/activity/typeAct20side/quickGetMilestoneAward',
    '/activity/typeAct20side/quickRecycle',
    '/activity/typeAct3d0/gacha',
    '/activity/typeAct3d0/getGachaInfo',
    '/activity/typeAct3d0/getMilestoneReward',
    '/activity/typeAct3d0/selectFaction',
    '/activity/typeAct4d0/finishStory',
    '/activity/typeAct4d0/getReward',
    '/activity/typeAct4d0/unlockStory',
    '/activity/typeAct5d0/getReward',
    '/activity/typeAct5d1/buyGoods',
    '/activity/typeAct5d1/buyRune',
    '/activity/typeAct5d1/getGoodsList',
    '/activity/typeAct5d1/getInfo',
    '/activity/typeAct9d0/readNews',
    '/activity/vecBreakV2/battleFinish',
    '/activity/vecBreakV2/battleStart',
    '/activity/vecBreakV2/changeBuffList',
    '/activity/vecBreakV2/defendBattleFinish',
    '/activity/vecBreakV2/defendBattleStart',
    '/activity/vecBreakV2/setDefend',
    '/activity/year5General/getInfReward',
]


async def _dispatch(request: Request, path: str):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    result = _activity_action(call_obj, path)
    return result


def _endpoint(path: str):
    async def handler(request: Request):
        return await _dispatch(request, path)

    handler.__name__ = "activity_" + path.strip("/").removeprefix("activity/").replace("/", "_")
    return handler


for _path in ACTIVITY_PATHS:
    router.add_api_route(_path, _endpoint(_path), methods=["POST"], name=_endpoint(_path).__name__)
