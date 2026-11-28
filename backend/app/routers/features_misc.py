"""Account, social-card, settings and small feature endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import game_data, missions, payments
from ..db import now_ts
from ..feature_common import (
    as_bool,
    as_dict,
    as_int,
    auth_error,
    body_id,
    call,
    ensure_dict,
    ensure_list,
    fail,
    finish,
    first_value,
    merge_delta,
    unique_append,
)


router = APIRouter()


@router.post("/setting/friendPrivate")
async def setting_friend_private(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    value = first_value(call_obj.body, "friendPrivate", "value", "private", default=0)
    ensure_dict(call_obj.user, "status")["friendPrivate"] = 1 if as_bool(value) or as_int(value) == 1 else 0
    return finish(call_obj, "status")


@router.post("/setting/perf/setLowPower")
async def setting_set_low_power(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    value = first_value(call_obj.body, "lowPower", "value", "enabled", default=False)
    settings = ensure_dict(call_obj.user, "settings")
    settings["lowPower"] = as_bool(value)
    ensure_dict(call_obj.user, "status")["lowPower"] = settings["lowPower"]
    return finish(call_obj, "status", "settings")


def _reslock_lock(call_obj, kind: str, locked: bool):
    root = ensure_dict(call_obj.user, "reslock")
    bucket = ensure_dict(root, kind)
    item_id = body_id(call_obj.body, "itemId", "id")
    if not item_id:
        return fail("itemId required")
    inst_id = str(first_value(call_obj.body, "instId", "instanceId", default="0") or "0")
    if locked:
        count = max(1, as_int(first_value(call_obj.body, "count", "cnt", default=1), 1))
        node = ensure_dict(bucket, item_id)
        node[inst_id] = max(count, as_int(node.get(inst_id), 0))
    else:
        stack = bucket.get(item_id)
        if isinstance(stack, dict):
            stack.pop(inst_id, None)
            if not stack:
                bucket.pop(item_id, None)
    return finish(call_obj, "reslock")


@router.post("/reslock/lockInventory")
async def reslock_lock_inventory(request: Request):
    call_obj = await call(request)
    return _reslock_lock(call_obj, "inventory", True) if call_obj else auth_error()


@router.post("/reslock/unlockInventory")
async def reslock_unlock_inventory(request: Request):
    call_obj = await call(request)
    return _reslock_lock(call_obj, "inventory", False) if call_obj else auth_error()


@router.post("/reslock/lockConsumable")
async def reslock_lock_consumable(request: Request):
    call_obj = await call(request)
    return _reslock_lock(call_obj, "consumable", True) if call_obj else auth_error()


@router.post("/reslock/unlockConsumable")
async def reslock_unlock_consumable(request: Request):
    call_obj = await call(request)
    return _reslock_lock(call_obj, "consumable", False) if call_obj else auth_error()


@router.post("/medal/setCustomData")
async def medal_set_custom_data(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    medal = ensure_dict(call_obj.user, "medal")
    custom = ensure_dict(medal, "custom")
    custom_map = ensure_dict(custom, "customs")
    custom_id = body_id(call_obj.body, "customId", "id", default="custom_1")
    payload = first_value(call_obj.body, "data", "custom", default={})
    custom_map[custom_id] = as_dict(payload)
    custom["selected"] = custom_id
    social = ensure_dict(call_obj.user, "social")
    board = ensure_dict(social, "medalBoard")
    board["type"] = "CUSTOM"
    board["custom"] = custom_id
    return finish(call_obj, "medal", "social")


@router.post("/medal/rewardMedal")
async def medal_reward_medal(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    medal_id = body_id(call_obj.body, "medalId", "id")
    if not medal_id:
        return fail("medalId required")
    medal = ensure_dict(call_obj.user, "medal")
    medals = ensure_dict(medal, "medals")
    node = medals.get(medal_id)
    if not isinstance(node, dict):
        node = {"id": medal_id, "val": [], "fts": -1, "rts": -1}
        medals[medal_id] = node
    node["rts"] = now_ts()
    ensure_dict(call_obj.user, "social").setdefault("medals", [])
    if medal_id not in call_obj.user["social"]["medals"]:
        call_obj.user["social"]["medals"].append(medal_id)
    return finish(call_obj, "medal", "social")


@router.post("/businessCard/editNameCard")
async def business_card_edit_name_card(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    style = ensure_dict(call_obj.user, "nameCardStyle")
    body = call_obj.body
    flag = as_int(body.get("flag"), 0)
    content = as_dict(body.get("content") or body.get("data"))
    if flag & 1 or isinstance(content.get("component"), list):
        components = content.get("component")
        if isinstance(components, list):
            style["componentOrder"] = components
    if flag & 2 or content.get("skinId"):
        skin = ensure_dict(style, "skin")
        skin["selected"] = first_value(content, "skinId", "selected", default=skin.get("selected", "nc_rhodes_default"))
    if flag & 4 or isinstance(content.get("misc"), dict):
        style["misc"] = as_dict(content.get("misc") or style.get("misc"))
    if flag & 8 or content.get("skinTmpl") is not None:
        ensure_dict(style, "skin")["tmpl"] = content.get("skinTmpl", ensure_dict(style, "skin").get("tmpl"))
    return finish(call_obj, "nameCardStyle")


@router.post("/npcAudio/changeLan")
async def npc_audio_change_lan(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    lang = str(first_value(call_obj.body, "voiceLan", "lang", default="CN_MANDARIN"))
    char_id = body_id(call_obj.body, "charId", "id")
    audio = ensure_dict(call_obj.user, "npcAudio")
    if char_id:
        node = ensure_dict(audio, char_id)
        node["npcShowAudioInfoFlag"] = lang
    else:
        for node in audio.values():
            if isinstance(node, dict):
                node["npcShowAudioInfoFlag"] = lang
    ensure_dict(call_obj.user, "status")["voiceLan"] = lang
    return finish(call_obj, "npcAudio", "status")


@router.post("/mainline/enterCharVoiceRecord")
async def mainline_enter_char_voice_record(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    archive_id = body_id(call_obj.body, "archiveId", "id", "missionId", default="mission_archive_main_14")
    root = ensure_dict(ensure_dict(call_obj.user, "mainline"), "charVoiceRecord")
    node = ensure_dict(root, archive_id)
    node["isOpen"] = True
    node.setdefault("confirmEnterReward", False)
    nodes = ensure_dict(node, "nodes")
    if not nodes:
        nodes["main_node_1"] = 0
    return finish(call_obj, "mainline")


@router.post("/mainline/confirmCharVoiceRecordReward")
async def mainline_confirm_char_voice_record_reward(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    archive_id = body_id(call_obj.body, "archiveId", "id", "missionId", default="mission_archive_main_14")
    root = ensure_dict(ensure_dict(call_obj.user, "mainline"), "charVoiceRecord")
    node = ensure_dict(root, archive_id)
    node["confirmEnterReward"] = True
    for key in list(as_dict(node.get("nodes")).keys()):
        node["nodes"][key] = 1
    call_obj.extra["items"] = []
    return finish(call_obj, "mainline")


@router.post("/mainlineClue/unlockClue")
async def mainline_clue_unlock(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    clue_id = body_id(call_obj.body, "clueId", "id")
    if not clue_id:
        return fail("clueId required")
    clue = ensure_dict(ensure_dict(call_obj.user, "mainline"), "clue")
    state = ensure_dict(clue, "state")
    state[clue_id] = 1
    clue["unlock"] = True
    return finish(call_obj, "mainline")


@router.post("/mainlineClue/getRewards")
async def mainline_clue_get_rewards(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    reward_id = body_id(call_obj.body, "rewardId", "id")
    if not reward_id:
        return fail("rewardId required")
    clue = ensure_dict(ensure_dict(call_obj.user, "mainline"), "clue")
    rewards = ensure_dict(clue, "reward")
    if rewards.get(reward_id):
        return fail("reward already claimed")
    rewards[reward_id] = 1
    call_obj.extra["items"] = []
    return finish(call_obj, "mainline")


@router.post("/mission/confirmMultiGroupMissionList")
async def mission_confirm_multi_group_list(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    groups = call_obj.body.get("groupIds") or call_obj.body.get("groups") or call_obj.body.get("missionGroupIds")
    if not isinstance(groups, list):
        groups = [groups] if groups else []
    gained = []
    for group_id in groups:
        if not group_id:
            continue
        result = missions.confirm_group(call_obj.user, str(group_id))
        if result.get("result") == 0:
            gained.extend(result.get("items") or [])
        elif result.get("result") not in (0, 1):
            continue
    return finish(call_obj, "mission", "status")


@router.post("/social/setCardShowMedal")
async def social_set_card_show_medal(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    board = ensure_dict(ensure_dict(call_obj.user, "social"), "medalBoard")
    medal_ids = first_value(call_obj.body, "medalIds", "medals", "ids", default=[])
    board["medals"] = medal_ids if isinstance(medal_ids, list) else [medal_ids]
    board["type"] = "MEDAL" if board["medals"] else "EMPTY"
    return finish(call_obj, "social")


@router.post("/charm/setSquad")
async def charm_set_squad(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    charm = ensure_dict(call_obj.user, "charm")
    squad = first_value(call_obj.body, "squad", "slots", default=[])
    charm["squad"] = squad if isinstance(squad, list) else []
    if "squadId" in call_obj.body:
        charm["squadId"] = call_obj.body.get("squadId")
    return finish(call_obj, "charm")


@router.post("/templateTrap/setTrapSquad")
async def template_trap_set_squad(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    traps = ensure_dict(call_obj.user, "templateTrap")
    template_id = body_id(call_obj.body, "templateId", "id", default="default")
    node = ensure_dict(traps, template_id)
    node["squad"] = first_value(call_obj.body, "squad", "slots", default=[])
    return finish(call_obj, "templateTrap")


@router.post("/captcha/v1/register")
async def captcha_register(request: Request):
    """The client uses this only as a readiness probe for captcha services."""
    return {"result": 0, "status": 0, "captcha": "", "ticket": "local-captcha-ticket"}


@router.post("/online/v1/ping")
async def online_ping(request: Request):
    return {"result": 0, "status": 0, "time": now_ts(), "ts": now_ts()}


@router.post("/online/v1/loginout")
async def online_loginout(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    ensure_dict(call_obj.user, "status")["lastOnlineTs"] = now_ts()
    return finish(call_obj, "status")


@router.post("/pay/createOrderAppstore")
async def pay_create_order_appstore(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    order = payments.create_order(
        call_obj.account,
        store_id=call_obj.body.get("storeId"),
        good_id=call_obj.body.get("goodId"),
        platform="ios",
    )
    if not order:
        return fail("product not found")
    return {"result": 0, "orderId": order["order_id"], "order": payments.create_order_response(order)}


@router.post("/pay/confirmOrderAppstoreNew")
async def pay_confirm_order_appstore_new(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    order_id = body_id(call_obj.body, "orderId", "order_id")
    if not order_id:
        return fail("orderId required")
    payments.mark_paid(order_id)
    response = payments.confirm_order(call_obj.account, order_id, source="appstore")
    return response
