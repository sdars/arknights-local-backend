import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import APP_CODE, CLIENT_VERSION, RES_VERSION
from ..services import account_by_phone, account_by_token, create_account
from .. import payments, product_catalog

router = APIRouter()


def ok(data: Any = None):
    body = {"status": 0, "msg": "OK", "type": "A"}
    if data is not None:
        body["data"] = data
    return body


def sdk_data(data: Any):
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return {"status": 0, "msg": "OK", "type": "A", "data": data}


@router.get("/general/v1/server_time")
async def server_time():
    return {"msg": "OK", "data": {"serverTime": int(time.time()), "isHoliday": True}, "status": 0}


@router.post("/general/v1/send_phone_code")
async def send_phone_code():
    return {"msg": "OK", "status": 0, "type": "A"}


@router.get("/app/v1/config")
async def app_config():
    path = Path(__file__).resolve().parents[2] / "fixtures" / "app_config.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "data": {
            "app": {
                "enablePayment": True,
                "enableAutoLogin": True,
                "enableAuthenticate": False,
                "enableAntiAddiction": False,
                "enableUnbindGrant": True,
                "enablePaidApp": False,
                "needShowName": False,
                "enableScanLogin": False,
                "enableGiftCode": False,
                "deviceCheckMode": 0,
                "appName": "明日方舟",
                "appAmount": 0,
                "customerServiceUrl": "",
                "giftCodeUrl": "",
                "appPurchaseUrl": "",
                "wechatAppId": "",
                "alipayAppId": "",
                "oneLoginAppId": "whatareyoulookingabout",
                "needAntiAddictionAlert": False,
                "enableScanCamera": False,
                "showCustomerService": False,
                "showAccountAgreement": True,
                "enableScanAppList": [],
                "gameAgreement": [],
            },
            "agreementUrl": {},
            "antiAddiction": {"minorPeriodStart": 20, "minorPeriodEnd": 21},
            "payment": [],
            "scanUrl": {"login": "hypergryph://scan_login"},
            "userCenterUrl": "",
        },
        "msg": "OK",
        "status": 0,
        "type": "A",
    }


@router.get("/user/info/v1/basic")
async def basic(token: str = ""):
    account = account_by_token(token)
    if not account:
        return {"status": 1, "msg": "Invalid Token"}
    return {
        "status": 0,
        "msg": "OK",
        "data": {
            "hgId": "1",
            "phone": account["phone"],
            "email": account["phone"],
            "identityNum": str(account["uid"]),
            "identityName": account["phone"],
            "isMinor": False,
            "isLatestUserAgreement": True,
            "updatedAgreement": [],
        },
    }


@router.post("/user/oauth2/v2/grant")
async def grant(request: Request):
    body = await request.json()
    account = account_by_token(body.get("token", ""))
    if not account:
        return {"status": 1, "msg": "Invalid Token"}
    return {
        "status": 0,
        "msg": "OK",
        "data": {"code": account["token"], "token": account["token"], "uid": str(account["uid"])},
        "type": "A",
    }


@router.post("/user/auth/v2/token_by_phone_code")
async def token_by_phone_code(request: Request):
    body = await request.json()
    phone = str(body.get("phone", ""))
    if not phone:
        return {"status": 1, "msg": "phone required"}
    account = account_by_phone(phone)
    if not account:
        account, _ = create_account(phone)
    return sdk_data({"token": account["token"], "deviceToken": "dev_" + account["token"]})


@router.post("/user/info/v1/update_agreement")
async def update_agreement():
    return {"status": 0, "msg": "OK", "type": "A"}


@router.get("/user/info/v1/agreement_version")
async def agreement_version():
    return {"status": 0, "msg": "OK", "type": "A", "data": "1"}


@router.post("/user/online/v1/loginout")
async def loginout():
    return {"status": 0, "msg": "OK", "type": "A"}


@router.post("/u8/user/v1/getToken")
async def u8_get_token(request: Request):
    body = await request.json()
    ext = body.get("extension", {})
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except Exception:
            ext = {}
    token = ext.get("code") or ext.get("token") or body.get("token", "")
    account = account_by_token(token)
    if not account:
        return {"result": 2, "error": "无法查询到此账户"}
    return {
        "result": 0,
        "uid": account["uid"],
        "error": "",
        "channelUid": account["uid"],
        "token": account["token"],
        "isGuest": 0,
        "extension": "{\"isGuest\":false}",
    }


@router.post("/u8/pay/getAllProductList")
async def product_list():
    return {"productList": product_catalog.active_products()}


@router.post("/user/pay/order/v2/create/app_product")
async def app_product(request: Request):
    body = await request.json()
    account = account_by_token(body.get("token", ""))
    if not account:
        return {"status": 1, "msg": "Invalid Token", "type": "A"}
    return payments.sdk_app_product(account, body.get("platform"))


@router.post("/user/pay/order/v2/create/app_product/alipay")
async def app_product_alipay(request: Request):
    body = await request.json()
    account = account_by_token(body.get("token", ""))
    if not account:
        return {"status": 1, "msg": "Invalid Token", "type": "A"}
    return payments.sdk_bill(account, "alipay")


@router.post("/user/pay/order/v2/create/app_product/wechat")
async def app_product_wechat(request: Request):
    body = await request.json()
    account = account_by_token(body.get("token", ""))
    if not account:
        return {"status": 1, "msg": "Invalid Token", "type": "A"}
    return payments.sdk_bill(account, "wechat")


@router.post("/u8/pay/confirmOrderState")
async def confirm_order_state(request: Request):
    body = await request.json()
    order = None
    order_id = str(body.get("orderId") or body.get("outTradeNo") or "")
    if order_id:
        order = payments.order_by_id(order_id)
    if not order:
        account = account_by_token(str(body.get("token", "")))
        if account:
            order = payments.latest_order(account)
    if order and int(order["status"] or 0) == 1:
        return {"status": 4, "message": "SUCCEED", "payState": 3, "result": 0}
    if order and int(order["status"] or 0) == 2:
        return {"status": 2, "message": "REJECTED", "payState": 2, "result": 1}
    return {"status": 1, "message": "PENDING", "payState": 1, "result": 0}


@router.post("/pay/confirmOrderState")
async def confirm_order_state_legacy(request: Request):
    return await confirm_order_state(request)


@router.get("/api/gate/meta/Android")
async def gate_meta():
    return {"preAnnounceId": "528", "actived": True, "preAnnounceType": 2}

