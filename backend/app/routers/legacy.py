"""Explicit implementations for the legacy protocol endpoints.

These paths used to fall through the catch-all route in ``main.py`` and return
an empty successful delta.  They are registered explicitly now so the client
either gets real state or sees a proper error.
"""

from fastapi import APIRouter, Request

from .. import game_data, grants, runtime_settings
from ..db import connect, now_ts
from ..services import (
    account_by_phone,
    account_by_token,
    create_account,
    player_data,
    save_player,
)


router = APIRouter()


def _account(request: Request):
    token = request.headers.get("secret") or request.headers.get("token") or ""
    return account_by_token(token)


def _delta(user):
    return {"deleted": {}, "modified": {key: user.get(key) for key in ("status", "inventory", "consumable", "troop", "dungeon", "social") if key in user}}


def _result(code=0, user=None, extra=None, **kwargs):
    body = {"result": code, "playerDataDelta": _delta(user or {})}
    if isinstance(extra, dict):
        body.update(extra)
    body.update(kwargs)
    return body


def _sdk_ok(data=None):
    body = {"status": 0, "msg": "OK", "type": "A"}
    if data is not None:
        body["data"] = data
    return body


def _sdk_data(data):
    import json

    return {"status": 0, "msg": "OK", "type": "A", "data": json.dumps(data, ensure_ascii=False, separators=(",", ":"))}


def _stage_node(user, stage_id):
    dungeon = user.setdefault("dungeon", {})
    stages = dungeon.setdefault("stages", {})
    node = stages.get(stage_id)
    if not isinstance(node, dict):
        node = {
            "stageId": stage_id,
            "state": 0,
            "completeTimes": 0,
            "startTimes": 0,
            "practiceTimes": 0,
            "hasBattleReplay": 0,
        }
        stages[stage_id] = node
    return node


def _stage_delta(user, stage_id):
    return {"deleted": {}, "modified": {"dungeon": {"stages": {stage_id: user["dungeon"]["stages"][stage_id]}}}}


@router.post("/quest/finishStoryStage")
async def finish_story_stage(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    body = await request.json()
    stage_id = str(body.get("stageId") or body.get("stage_id") or "")
    if not stage_id or not game_data.stage(stage_id):
        return _result(1)
    user = player_data(account)
    node = _stage_node(user, stage_id)
    node["state"] = 3
    node["completeTimes"] = max(1, int(node.get("completeTimes", 0) or 0) + 1)
    node["startTimes"] = max(1, int(node.get("startTimes", 0) or 0))
    save_player(account, user)
    return _result(0, extra={"playerDataDelta": _stage_delta(user, stage_id)})


@router.post("/quest/unlockStageFog")
async def unlock_stage_fog(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    body = await request.json()
    stage_id = str(body.get("stageId") or body.get("stage_id") or "")
    if not stage_id:
        return _result(1)
    user = player_data(account)
    node = _stage_node(user, stage_id)
    node.setdefault("state", 0)
    save_player(account, user)
    return _result(0, extra={"playerDataDelta": _stage_delta(user, stage_id)})


@router.post("/quest/unlockHideStage")
async def unlock_hide_stage(request: Request):
    return await unlock_stage_fog(request)


@router.post("/quest/getCowLevelReward")
async def get_cow_level_reward(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    user = player_data(account)
    body = await request.json()
    stage_id = str(body.get("stageId") or body.get("stage_id") or "")
    if not stage_id:
        return _result(1)
    node = _stage_node(user, stage_id)
    node["cowLevelReward"] = 1
    save_player(account, user)
    return _result(0, extra={"items": [], "playerDataDelta": _stage_delta(user, stage_id)})


@router.post("/quest/getMainlineRecordRewards")
async def get_mainline_record_rewards(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    user = player_data(account)
    body = await request.json()
    reward_id = str(body.get("rewardId") or body.get("id") or "")
    mainline = user.setdefault("mainline", {})
    claimed = mainline.setdefault("recordRewards", [])
    if reward_id and reward_id not in claimed:
        claimed.append(reward_id)
    save_player(account, user)
    return _result(0, extra={"items": [], "playerDataDelta": {"deleted": {}, "modified": {"mainline": mainline}}})


@router.post("/quest/getMainlineCache")
async def get_mainline_cache(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    user = player_data(account)
    body = await request.json()
    cache_id = str(body.get("cacheId") or body.get("id") or "")
    mainline = user.setdefault("mainline", {})
    claimed = mainline.setdefault("cacheRewards", [])
    if cache_id and cache_id not in claimed:
        claimed.append(cache_id)
    save_player(account, user)
    return _result(0, extra={"items": [], "playerDataDelta": {"deleted": {}, "modified": {"mainline": mainline}}})


@router.post("/quest/confirmSixStarReward")
async def confirm_six_star_reward(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    user = player_data(account)
    body = await request.json()
    group_id = str(body.get("groupId") or "")
    mainline = user.setdefault("mainline", {})
    claimed = mainline.setdefault("sixStarRewards", [])
    if group_id and group_id not in claimed:
        claimed.append(group_id)
    save_player(account, user)
    return _result(0, extra={"items": [], "playerDataDelta": {"deleted": {}, "modified": {"mainline": mainline}}})


@router.post("/quest/editStageSixStarTag")
async def edit_stage_six_star_tag(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    body = await request.json()
    stage_id = str(body.get("stageId") or "")
    if not stage_id:
        return _result(1)
    user = player_data(account)
    node = _stage_node(user, stage_id)
    selected = body.get("selected")
    node["sixStarTag"] = selected if isinstance(selected, list) else []
    save_player(account, user)
    return _result(0, extra={"playerDataDelta": _stage_delta(user, stage_id)})


@router.post("/user/bindNickName")
async def bind_nickname(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    body = await request.json()
    nickname = str(body.get("nickName") or "").strip()
    if not nickname or len(nickname) > 16:
        return _result(1)
    user = player_data(account)
    user.setdefault("status", {})["nickName"] = nickname
    save_player(account, user)
    return _result(0, extra={"result": 0, "playerDataDelta": {"deleted": {}, "modified": {"status": user["status"]}}})


@router.post("/user/bindBirthday")
async def bind_birthday(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    body = await request.json()
    month = int(body.get("month", 0) or 0)
    day = int(body.get("day", 0) or 0)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return _result(1)
    user = player_data(account)
    user.setdefault("status", {})["birthday"] = {"month": month, "day": day}
    save_player(account, user)
    return _result(0, extra={"playerDataDelta": {"deleted": {}, "modified": {"status": user["status"]}}})


@router.post("/user/changeAvatar")
async def change_avatar(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    body = await request.json()
    user = player_data(account)
    status = user.setdefault("status", {})
    status["avatarId"] = str(body.get("id") or body.get("avatarId") or "")
    status["avatar"] = {"type": body.get("type", 0), "id": status["avatarId"]}
    save_player(account, user)
    return _result(0, extra={"playerDataDelta": {"deleted": {}, "modified": {"status": status}}})


@router.post("/user/buyAp")
async def buy_ap(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    user = player_data(account)
    status = user.setdefault("status", {})
    key = grants.diamond_status_key(user)
    if int(status.get(key, 0) or 0) < 1:
        return _result(1, user)
    status[key] = int(status.get(key, 0) or 0) - 1
    status["ap"] = min(int(status.get("maxAp", 0) or 0), int(status.get("ap", 0) or 0) + 1)
    save_player(account, user)
    return _result(0, extra={"result": 0, "playerDataDelta": {"deleted": {}, "modified": {"status": status}}})


def _take_inventory(user, item_id, count):
    inventory = user.setdefault("inventory", {})
    current = int(inventory.get(item_id, 0) or 0)
    if current < count:
        consumable = user.setdefault("consumable", {})
        node = consumable.get(item_id)
        slot = node.get("0") if isinstance(node, dict) else None
        if isinstance(slot, dict):
            current = int(slot.get("count", 0) or 0)
            if current < count:
                return False
            slot["count"] = current - count
            return True
        return False
    inventory[item_id] = current - count
    return True


def _use_one(user, raw):
    if not isinstance(raw, dict):
        return False
    item_id = str(raw.get("itemId") or "")
    count = max(1, int(raw.get("cnt", raw.get("count", 1)) or 1))
    if not item_id or not _take_inventory(user, item_id, count):
        return False
    meta = (game_data.excel("item_table") or {}).get("items", {}).get(item_id, {})
    kind = str(meta.get("itemType") or "")
    if kind == "AP_SUPPLY":
        amount = 100 if "lt_100" in item_id else 60 if "lt_60" in item_id else 10
        status = user.setdefault("status", {})
        status["ap"] = min(int(status.get("maxAp", 0) or 0), int(status.get("ap", 0) or 0) + amount * count)
    return True


@router.post("/user/useItem")
async def use_item(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    body = await request.json()
    user = player_data(account)
    if not _use_one(user, body):
        return _result(1, user, items=[])
    save_player(account, user)
    return _result(0, extra={"items": [], "playerDataDelta": _delta(user)})


@router.post("/user/useItems")
async def use_items(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    body = await request.json()
    user = player_data(account)
    items = body.get("items")
    if not isinstance(items, list) or not items or not all(_use_one(user, raw) for raw in items):
        return _result(1, user, items=[])
    save_player(account, user)
    return _result(0, extra={"items": [], "playerDataDelta": _delta(user)})


@router.post("/user/useRenameCard")
async def use_rename_card(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    body = await request.json()
    nickname = str(body.get("nickName") or "").strip()
    if not nickname or len(nickname) > 16:
        return _result(1)
    user = player_data(account)
    item_id = str(body.get("itemId") or "")
    if item_id and not _take_inventory(user, item_id, 1):
        return _result(1, user)
    user.setdefault("status", {})["nickName"] = nickname
    save_player(account, user)
    return _result(0, extra={"result": 0, "playerDataDelta": {"deleted": {}, "modified": {"status": user["status"]}}})


@router.post("/user/receiveTeamCollectionReward")
async def receive_team_collection_reward(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    body = await request.json()
    user = player_data(account)
    team = user.setdefault("teamCollection", {})
    reward_id = str(body.get("rewardId") or "")
    received = team.setdefault("received", [])
    if reward_id and reward_id not in received:
        received.append(reward_id)
    save_player(account, user)
    return _result(0, extra={"items": [], "playerDataDelta": {"deleted": {}, "modified": {"teamCollection": team}}})


@router.post("/user/recvLongTermCheckInReward")
async def receive_long_term_checkin(request: Request):
    account = _account(request)
    if not account:
        return _result(2)
    user = player_data(account)
    long_term = user.setdefault("longTermCheckIn", {})
    long_term["lastRewardTs"] = now_ts()
    save_player(account, user)
    return _result(0, extra={"items": [], "playerDataDelta": {"deleted": {}, "modified": {"longTermCheckIn": long_term}}})


@router.post("/user/login")
@router.post("/user/loginBySmsCode")
@router.post("/user/register")
async def sdk_login(request: Request):
    body = await request.json()
    phone = str(body.get("phone") or body.get("account") or "")
    if not phone:
        return {"status": 1, "msg": "phone required", "type": "A"}
    account = account_by_phone(phone)
    if not account:
        account, _ = create_account(phone)
    return _sdk_data({"token": account["token"], "uid": str(account["uid"]), "deviceToken": "dev_" + account["token"]})


@router.post("/user/v1/guestLogin")
async def guest_login(request: Request):
    body = await request.json()
    device = str(body.get("deviceId") or body.get("device_id") or now_ts())
    phone = "guest_" + device
    account = account_by_phone(phone)
    if not account:
        account, _ = create_account(phone, nickname="访客")
    return _sdk_data({"token": account["token"], "uid": str(account["uid"]), "deviceToken": "dev_" + account["token"]})


@router.post("/user/sendSmsCode")
async def sdk_send_sms_code(request: Request):
    body = await _json_body(request)
    phone = str(body.get("account") or body.get("phone") or "")
    return _sdk_data({"code": "000000", "phone": phone})


async def _json_body(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}


@router.post("/user/auth")
async def sdk_auth(request: Request):
    body = await _json_body(request)
    token = str(body.get("token") or body.get("secret") or "")
    account = account_by_token(token)
    if not account:
        return {"status": 1, "msg": "Invalid Token", "type": "A"}
    return _sdk_data({"token": account["token"], "uid": str(account["uid"]), "deviceToken": "dev_" + account["token"]})


@router.post("/user/authenticateUserIdentity")
async def sdk_authenticate_identity(request: Request):
    body = await _json_body(request)
    return _sdk_ok({"verified": bool(body.get("idCardNum") or body.get("name") or True)})


@router.post("/user/checkIdCard")
async def sdk_check_id_card(request: Request):
    return _sdk_ok({"result": 0, "isMinor": False, "message": "OK"})


@router.post("/user/changePassword")
async def sdk_change_password(request: Request):
    return _sdk_ok({"result": 0})


@router.post("/user/changePhone")
async def sdk_change_phone(request: Request):
    body = await _json_body(request)
    token = str(body.get("token") or request.headers.get("secret") or "")
    account = account_by_token(token)
    new_phone = str(body.get("newPhone") or body.get("phone") or "")
    if not account:
        return {"status": 1, "msg": "Invalid Token", "type": "A"}
    if not new_phone:
        return _sdk_ok({"result": 1, "errMsg": "newPhone required"})
    try:
        with connect() as conn:
            conn.execute("UPDATE accounts SET phone=? WHERE id=?", (new_phone, account["id"]))
    except Exception:
        return _sdk_ok({"result": 1, "errMsg": "phone already used"})
    return _sdk_ok({"result": 0, "phone": new_phone})


@router.post("/user/changePhoneCheck")
async def sdk_change_phone_check(request: Request):
    return _sdk_ok({"result": 0})


@router.post("/user/updateAgreement")
async def sdk_update_agreement(request: Request):
    body = await _json_body(request)
    token = str(body.get("token") or request.headers.get("secret") or "")
    account = account_by_token(token)
    if account:
        user = player_data(account)
        user.setdefault("status", {})["agreementVersion"] = str(body.get("version") or body.get("agreementVersion") or "")
        save_player(account, user)
    return _sdk_ok({"result": 0})


@router.api_route("/user/info/v1/cloud_auth", methods=["GET", "POST"])
@router.api_route("/user/info/v1/send_phone_code", methods=["GET", "POST"])
@router.api_route("/user/info/v1/verify_cloud_auth_result", methods=["GET", "POST"])
@router.api_route("/user/oauth2/v1/unbind_grant", methods=["GET", "POST"])
async def cloud_auth_success(request: Request):
    body = await _json_body(request)
    path = request.url.path
    token = str(body.get("token") or request.headers.get("secret") or request.query_params.get("token") or "")
    account = account_by_token(token)
    if path.endswith("send_phone_code"):
        return _sdk_ok({"code": "000000", "phone": body.get("phone") or body.get("account") or ""})
    if path.endswith("verify_cloud_auth_result"):
        return _sdk_ok({"result": 0, "verifyStatus": 1})
    if path.endswith("unbind_grant"):
        return _sdk_ok({"result": 0})
    return _sdk_ok({"needCloudAuth": False, "token": token if account else ""})


@router.api_route("/user/info/v1/need_cloud_auth", methods=["GET", "POST"])
async def need_cloud_auth():
    return _sdk_ok({"needCloudAuth": False})


@router.api_route("/user/oauth2/v1/grant", methods=["GET", "POST"])
async def oauth_v1_grant(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    account = account_by_token(str(body.get("token") or request.headers.get("secret") or request.query_params.get("token") or ""))
    if not account:
        return {"status": 1, "msg": "Invalid Token", "type": "A"}
    return _sdk_ok({"code": account["token"], "token": account["token"], "uid": str(account["uid"])})


@router.post("/admin/getVersion")
async def get_version():
    settings = runtime_settings.values()
    return {
        "result": 0,
        "clientVersion": settings["client_version"],
        "resVersion": settings["res_version"],
        "version": settings["client_version"],
        "PkgVersion": settings["client_version"],
        "ResVersion": settings["res_version"],
    }
