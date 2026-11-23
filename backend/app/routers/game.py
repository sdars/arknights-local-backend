import json
import logging
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import BATTLE_START_TEMPLATE
from ..db import now_ts
from ..services import account_by_token, player_data, save_player, touch_login
from ..shop_fixtures import shop_response
from .. import (
    battles,
    game_data,
    grants,
    mail_service,
    missions,
    monthly_card,
    payments,
    runtime_settings,
    shop_service,
)

router = APIRouter()
logger = logging.getLogger(__name__)


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
    return {
        "result": {},
        "ts": now_ts(),
        "pushMessage": [],
        **empty_delta(),
    }


@router.post("/account/syncPushMessage")
async def sync_push_message():
    return {"result": 0, "pushMessage": [], **empty_delta()}


@router.post("/quest/battleStart")
async def battle_start(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    body = await request.json()
    template = json.loads(BATTLE_START_TEMPLATE.read_text(encoding="utf-8"))
    battle_id = str(uuid.uuid4())
    template["battleId"] = battle_id
    template["result"] = 0
    modified = template.setdefault("playerDataDelta", {}).setdefault("modified", {})
    status = modified.setdefault("status", {})
    status["uid"] = str(account["uid"])
    status["nickName"] = account["nickname"] or "博士"
    status["serverName"] = runtime_settings.values()["server_name"]
    stage = body.get("stageId", "guide_01")
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
    battles.record_battle_start(account["id"], battle_id, stage)
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
        grants.merge_delta(delta, monthly_delta)
        if monthly_granted:
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
    monthly_granted, monthly_delta = monthly_card.grant_daily(user, now=ts)
    grants.merge_delta(delta, monthly_delta)
    save_player(account, user)
    return {
        "result": 0,
        "signInRewards": granted,
        "subscriptionRewards": monthly_granted,
        "playerDataDelta": delta,
    }


def _open_server_v3_claim(account, body):
    user = player_data(account)
    v3 = user.setdefault("openServerV3", {})
    if not isinstance(v3, dict):
        v3 = {}
        user["openServerV3"] = v3
    checkin = v3.setdefault("checkIn", {})
    if not isinstance(checkin, dict):
        checkin = {}
        v3["checkIn"] = checkin
    group_id = str(checkin.get("groupId") or "new_login_g_1")
    checkin["groupId"] = group_id
    history = checkin.get("history")
    if not isinstance(history, list):
        history = []
        checkin["history"] = history
    normalized = []
    for value in history:
        normalized.append(1 if _as_int(value, 1) else 0)
    history[:] = normalized

    index = _as_int(body.get("index"), 0)
    if index < 0:
        index = next((i for i, value in enumerate(history) if value == 1), 0)
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
    save_player(account, user)
    return {
        "result": 0,
        "playerDataDelta": {"deleted": {}, "modified": {"troop": user.get("troop", {})}},
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


@router.post("/charBuild/batchSetCharVoiceLan")
async def batch_set_voice():
    return {"result": 0, **empty_delta()}


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
async def social_good_list():
    return shop_response("SocialGoodList.json")


@router.post("/shop/getClassicGoodList")
async def classic_good_list():
    return shop_response("ClassicGoodList.json")


@router.post("/shop/getSkinGoodList")
async def skin_good_list():
    return shop_response("SkinGoodList.json")


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

