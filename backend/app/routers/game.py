import copy
import json
import time
import uuid

from fastapi import APIRouter, Request

from ..config import BATTLE_START_TEMPLATE, CLIENT_VERSION, RES_VERSION, SERVER_NAME
from ..db import now_ts
from ..services import account_by_token, player_data, save_player, touch_login

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
    status["lastOnlineTs"] = now_ts()
    save_player(account, user)
    return {"result": 0, "ts": now_ts(), "user": user, **empty_delta()}


@router.post("/account/syncStatus")
async def sync_status():
    return {"result": 0, "pushMessage": [], **empty_delta()}


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


@router.post("/story/finishStory")
async def finish_story():
    return {"result": 0, **empty_delta()}

