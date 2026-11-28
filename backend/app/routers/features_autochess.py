"""Auto-chess permanent mode endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import social as social_service
from ..db import now_ts
from ..feature_common import (
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
    set_deleted,
    unique_append,
)


router = APIRouter()


def _auto(call_obj):
    return ensure_dict(call_obj.user, "autochessPerm")


def _game(call_obj):
    return ensure_dict(_auto(call_obj), "game")


@router.post("/autoChessPerm/createTeam")
async def auto_create_team(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    game = _game(call_obj)
    team_id = body_id(call_obj.body, "teamId", "id", default=f"team_{call_obj.account['uid']}")
    game["team"] = {
        "teamId": team_id,
        "leaderUid": str(call_obj.account["uid"]),
        "members": [str(call_obj.account["uid"])],
        "createdTs": now_ts(),
    }
    game["state"] = "TEAM"
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/joinTeam")
async def auto_join_team(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    game = _game(call_obj)
    team_id = body_id(call_obj.body, "teamId", "id", default=f"team_{call_obj.account['uid']}")
    team = ensure_dict(game, "team")
    team["teamId"] = team_id
    unique_append(ensure_list(team, "members"), str(call_obj.account["uid"]))
    game["state"] = "TEAM"
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/queryMatch")
async def auto_query_match(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    game = _game(call_obj)
    state = str(game.get("state") or "NONE")
    entries = ensure_list(game, "matchQueue")
    return {
        "result": 0,
        "status": 1 if state == "MATCHING" else 2 if state == "BATTLE" else 0,
        "queueSize": len(entries),
        "playerDataDelta": {"deleted": {}, "modified": {"autochessPerm": _auto(call_obj)}},
    }


@router.post("/autoChessPerm/startMatch")
async def auto_start_match(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    game = _game(call_obj)
    game["state"] = "BATTLE"
    game["battleId"] = battle_id("autochess", call_obj.account)
    game["startTs"] = now_ts()
    result = finish(call_obj, "autochessPerm")
    result["battleId"] = game["battleId"]
    return result


@router.post("/autoChessPerm/quitGame")
async def auto_quit_game(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    game = _game(call_obj)
    game["state"] = "NONE"
    game["quitTs"] = now_ts()
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/multiBattleStart")
async def auto_multi_battle_start(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    game = _game(call_obj)
    game["state"] = "BATTLE"
    game["battleId"] = battle_id("autochess-multi", call_obj.account)
    game["battleMode"] = "MULTI"
    result = finish(call_obj, "autochessPerm")
    result["battleId"] = game["battleId"]
    return result


@router.post("/autoChessPerm/multiBattleFinish")
async def auto_multi_battle_finish(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    game = _game(call_obj)
    game["state"] = "SETTLE"
    game["lastResult"] = "WIN" if as_int(call_obj.body.get("win"), 1) else "LOSE"
    game["finishTs"] = now_ts()
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/startGuideBattle")
async def auto_start_guide_battle(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    game = _game(call_obj)
    game["state"] = "GUIDE"
    game["battleId"] = battle_id("autochess-guide", call_obj.account)
    result = finish(call_obj, "autochessPerm")
    result["battleId"] = game["battleId"]
    return result


@router.post("/autoChessPerm/finishGuideBattle")
async def auto_finish_guide_battle(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    rule = body_id(call_obj.body, "ruleId", "id", default="guide_1")
    _game(call_obj)["state"] = "NONE"
    ensure_dict(_auto(call_obj), "trainingModeFin")[rule] = 1
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/settleGame")
async def auto_settle_game(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    auto = _auto(call_obj)
    game = ensure_dict(auto, "game")
    season_id = body_id(call_obj.body, "seasonId", "season", default="season_1")
    score = max(0, as_int(call_obj.body.get("score"), 0))
    seasons = ensure_dict(auto, "seasons")
    record = ensure_dict(seasons, season_id)
    record["score"] = max(as_int(record.get("score"), 0), score)
    record["lastSettleTs"] = now_ts()
    game["state"] = "NONE"
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/settleLike")
async def auto_settle_like(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    game = _game(call_obj)
    uid = body_id(call_obj.body, "uid", "targetUid")
    if uid:
        unique_append(ensure_list(game, "liked"), uid)
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/report")
async def auto_report(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    game = _game(call_obj)
    updates = {key: value for key, value in call_obj.body.items() if key not in {"secret", "token"}}
    ensure_dict(game, "report").update(updates)
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/syncInfo")
async def auto_sync_info(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    auto = _auto(call_obj)
    return {
        "result": 0,
        "info": {"game": auto.get("game") or {}, "shop": auto.get("shop") or {}, "season": auto.get("seasons") or {}},
        "playerDataDelta": {"deleted": {}, "modified": {"autochessPerm": auto}},
    }


@router.post("/autoChessPerm/getFriendCharAssistList")
async def auto_get_friend_char_assist_list(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    friend = social_service.ensure_state(call_obj.user)
    rows = []
    for uid in social_service.friend_uids(friend)[:20]:
        target, target_user = social_service._target_user(uid)
        if not target or not target_user:
            continue
        chars = as_dict(ensure_dict(target_user, "troop").get("chars"))
        char_rows = [
            {
                "charId": char.get("charId"),
                "level": as_int(char.get("level"), 1),
                "evolvePhase": as_int(char.get("evolvePhase"), 0),
                "potentialRank": as_int(char.get("potentialRank"), 0),
                "skinId": char.get("skin"),
            }
            for char in chars.values()
            if isinstance(char, dict)
        ]
        if char_rows:
            rows.append({"uid": uid, "nickName": (target_user.get("status") or {}).get("nickName"), "chars": char_rows})
    return {"result": 0, "assistList": rows, "playerDataDelta": {"deleted": {}, "modified": {}}}


@router.post("/autoChessPerm/getGoodList")
async def auto_get_good_list(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    shop = ensure_dict(_auto(call_obj), "shop")
    return {
        "result": 0,
        "goodList": ensure_dict(shop, "permShop").get("info") or [],
        "playerDataDelta": {"deleted": {}, "modified": {"autochessPerm": _auto(call_obj)}},
    }


@router.post("/autoChessPerm/buyGood")
async def auto_buy_good(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    auto = _auto(call_obj)
    shop = ensure_dict(auto, "shop")
    perm = ensure_dict(shop, "permShop")
    goods = ensure_dict(perm, "good")
    good_id = body_id(call_obj.body, "goodId", "id")
    if not good_id:
        return fail("goodId required")
    good = ensure_dict(goods, good_id)
    if good.get("sold"):
        return fail("good already sold")
    count = as_int(call_obj.body.get("count"), 1)
    if count <= 0:
        return fail("invalid count")
    good["sold"] = True
    good["buyTs"] = now_ts()
    info = perm.get("info")
    if not isinstance(info, list):
        info = []
        perm["info"] = info
    info.append({"goodId": good_id, "count": count})
    if "coin" in call_obj.body:
        perm["coin"] = max(0, as_int(perm.get("coin"), 0) - max(0, as_int(call_obj.body.get("coin"), 0)))
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/recvAccumulativePrize")
async def auto_recv_accumulative_prize(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    auto = _auto(call_obj)
    prize_id = body_id(call_obj.body, "prizeId", "id")
    claimed = ensure_dict(auto, "accumulativePrizes")
    if not prize_id or claimed.get(prize_id):
        return fail("prize unavailable")
    claimed[prize_id] = 1
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/chooseSkin")
async def auto_choose_skin(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    skin = body_id(call_obj.body, "skinId", "id")
    if not skin:
        return fail("skinId required")
    auto = _auto(call_obj)
    owned = ensure_dict(auto, "skins")
    if skin not in owned:
        return fail("skin not owned")
    auto["skinSelect"] = skin
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/chooseSkinVoice")
async def auto_choose_skin_voice(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    auto = _auto(call_obj)
    skin = body_id(call_obj.body, "skinId", "id")
    voice = body_id(call_obj.body, "voiceLan", "voice")
    if skin:
        ensure_dict(auto, "skinVoice")[skin] = voice
    auto["skinSelect"] = skin or auto.get("skinSelect", "")
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/getSceneInfo")
async def auto_get_scene_info(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    auto = _auto(call_obj)
    return {"result": 0, "scene": auto.get("game") or {}, "playerDataDelta": {"deleted": {}, "modified": {"autochessPerm": auto}}}


@router.post("/autoChessPerm/removeChessPoolChar")
async def auto_remove_pool_char(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    char_id = body_id(call_obj.body, "charId", "id")
    pool = ensure_dict(_game(call_obj), "chessPool")
    pool.pop(char_id, None)
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/setChessPoolAssist")
async def auto_set_pool_assist(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    value = call_obj.body.get("assist") or call_obj.body.get("chars") or []
    ensure_dict(_game(call_obj), "chessPool")["assist"] = value if isinstance(value, list) else []
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/setChessPoolDeploy")
async def auto_set_pool_deploy(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    value = call_obj.body.get("deploy") or call_obj.body.get("slots") or []
    ensure_dict(_game(call_obj), "chessPool")["deploy"] = value if isinstance(value, list) else []
    return finish(call_obj, "autochessPerm")


@router.post("/autoChessPerm/setChessPoolDiyChar")
async def auto_set_pool_diy(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    value = call_obj.body.get("chars") or call_obj.body.get("diy") or []
    ensure_dict(_game(call_obj), "chessPool")["diy"] = value if isinstance(value, list) else []
    return finish(call_obj, "autochessPerm")
