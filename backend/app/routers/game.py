import copy
import json
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import BATTLE_START_TEMPLATE, CLIENT_VERSION, RES_VERSION, SERVER_NAME
from ..db import now_ts
from ..services import account_by_token, player_data, save_player, touch_login
from ..shop_fixtures import shop_response
from .. import payments

router = APIRouter()


def empty_delta():
    return {"playerDataDelta": {"deleted": {}, "modified": {}}}


def auth(request: Request):
    token = request.headers.get("secret") or request.headers.get("token") or ""
    return account_by_token(token)


@router.post("/account/login")
async def account_login(request: Request):
    body = await request.json()
    token = str(body.get("token", ""))
    account = account_by_token(token)
    if not account:
        return {"result": 2, "error": "invalid token"}
    if account["banned"]:
        return {"result": 1, "error": "account banned"}
    client_version = str(body.get("clientVersion", ""))
    assets_version = str(body.get("assetsVersion", ""))
    if client_version and client_version != CLIENT_VERSION:
        return {"result": 2, "error": "客户端版本需要更新"}
    if assets_version and assets_version != RES_VERSION:
        return {"result": 4, "error": "资源需要更新"}
    touch_login(account, body.get("deviceId"), request.client.host if request.client else None)
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
    status["serverName"] = SERVER_NAME
    ts = now_ts()
    status["lastOnlineTs"] = ts
    status["lastRefreshTs"] = ts
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
    template["battleId"] = str(uuid.uuid4())
    template["result"] = 0
    modified = template.setdefault("playerDataDelta", {}).setdefault("modified", {})
    status = modified.setdefault("status", {})
    status["uid"] = str(account["uid"])
    status["nickName"] = account["nickname"] or "博士"
    status["serverName"] = SERVER_NAME
    stage = body.get("stageId", "guide_01")
    stages = modified.setdefault("dungeon", {}).setdefault("stages", {})
    stages.setdefault(stage, {"hasBattleReplay": 0, "practiceTimes": 0, "state": 3, "completeTimes": 0, "stageId": stage, "startTimes": 1})
    return template


@router.post("/quest/battleFinish")
async def battle_finish(request: Request):
    account = auth(request)
    if not account:
        return {"result": 2, "error": "invalid token"}
    return {"result": 0, **empty_delta()}


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
    order = payments.create_order(account, body.get("storeId"), body.get("goodId"))
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
async def gp_good_list():
    return shop_response("GPGoodList.json")


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
async def good_purchase_state():
    return shop_response("GoodPurchaseState.json")

