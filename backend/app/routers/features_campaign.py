"""Permanent and rotating campaign (annihilation) endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import battles, game_data
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
    fail,
    finish,
    stage_ap_cost,
)


router = APIRouter()


def _campaign_state(call_obj):
    return ensure_dict(call_obj.user, "campaignsV2")


def _campaign_instances(call_obj):
    return ensure_dict(_campaign_state(call_obj), "instances")


def _instance(call_obj, stage_id: str):
    instances = _campaign_instances(call_obj)
    node = instances.get(stage_id)
    if not isinstance(node, dict):
        node = {"maxKills": 0, "rewardStatus": [0] * 8}
        instances[stage_id] = node
    statuses = node.get("rewardStatus")
    if not isinstance(statuses, list):
        node["rewardStatus"] = [0] * 8
    return node


def _campaign_def(stage_id: str) -> dict:
    table = game_data.excel("campaign_table.json") or {}
    return as_dict(as_dict(table.get("campaigns")).get(stage_id))


@router.post("/campaignV2/battleStart")
async def campaign_battle_start(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    stage_id = body_id(call_obj.body, "stageId", "stage")
    stage = game_data.stage(stage_id)
    if not stage or not _campaign_def(stage_id):
        return fail(f"unknown campaign stage: {stage_id}")
    cost = stage_ap_cost(stage, 0)
    status = ensure_dict(call_obj.user, "status")
    if as_int(status.get("ap"), 0) < cost:
        return fail("not enough sanity")
    status["ap"] = as_int(status.get("ap"), 0) - cost
    call_obj.touch("status")
    stages = ensure_dict(ensure_dict(call_obj.user, "dungeon"), "stages")
    node = ensure_dict(stages, stage_id)
    node["startTimes"] = as_int(node.get("startTimes"), 0) + 1
    node.setdefault("state", 0)
    node.setdefault("completeTimes", 0)
    node.setdefault("hasBattleReplay", 0)
    node.setdefault("practiceTimes", 0)
    node["stageId"] = stage_id
    call_obj.touch("dungeon")
    state = _campaign_state(call_obj)
    state["activeBattle"] = {"stageId": stage_id, "battleId": battle_id("camp", call_obj.account), "startTs": now_ts()}
    call_obj.touch("campaignsV2")
    result = finish(call_obj, "status", "dungeon", "campaignsV2")
    result["battleId"] = state["activeBattle"]["battleId"]
    return result


def _finish_campaign(call_obj):
    state = _campaign_state(call_obj)
    active = as_dict(state.get("activeBattle"))
    decoded = battles.decrypt_battle_data(
        call_obj.body.get("data"),
        (call_obj.user.get("pushFlags") or {}).get("status", ""),
    ) or {}
    stage_id = body_id(call_obj.body, "stageId", "stage", default=str(active.get("stageId") or ""))
    stage_id = str(decoded.get("stageId") or decoded.get("battleId") or stage_id or "")
    if not stage_id:
        return fail("stageId required")
    if not _campaign_def(stage_id):
        return fail(f"unknown campaign stage: {stage_id}")
    kills = max(
        0,
        as_int(
            decoded.get("killCnt")
            or decoded.get("killCount")
            or call_obj.body.get("killCnt")
            or call_obj.body.get("kills")
            or call_obj.body.get("maxKills")
            or 400,
            400,
        ),
    )
    instance = _instance(call_obj, stage_id)
    before = as_int(instance.get("maxKills"), 0)
    instance["maxKills"] = max(before, kills)
    stages = ensure_dict(ensure_dict(call_obj.user, "dungeon"), "stages")
    dungeon_node = ensure_dict(stages, stage_id)
    dungeon_node["completeTimes"] = as_int(dungeon_node.get("completeTimes"), 0) + 1
    dungeon_node["state"] = max(2, as_int(dungeon_node.get("state"), 0))
    dungeon_node["stageId"] = stage_id
    state.pop("activeBattle", None)
    call_obj.touch("campaignsV2", "dungeon")
    call_obj.extra["currentFeeBefore"] = before
    call_obj.extra["currentFeeAfter"] = instance["maxKills"]
    call_obj.extra["unlockStages"] = []
    call_obj.extra["rewards"] = []
    call_obj.extra["unusualRewards"] = []
    call_obj.extra["additionalRewards"] = []
    call_obj.extra["diamondMaterialRewards"] = []
    call_obj.extra["furnitureRewards"] = []
    return finish(call_obj, "campaignsV2", "dungeon")


@router.post("/campaignV2/battleFinish")
async def campaign_battle_finish(request: Request):
    call_obj = await call(request)
    return _finish_campaign(call_obj) if call_obj else auth_error()


@router.post("/campaignV2/battleSweep")
async def campaign_battle_sweep(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    stage_id = body_id(call_obj.body, "stageId", "stage")
    item_id = body_id(call_obj.body, "itemId", default="EXTERMINATION_AGENT")
    inst_id = body_id(call_obj.body, "instId", default="0")
    if not _campaign_def(stage_id):
        return fail(f"unknown campaign stage: {stage_id}")
    consumable = ensure_dict(call_obj.user, "consumable")
    stack = as_dict(consumable.get(item_id))
    slot = as_dict(stack.get(inst_id)) if stack else {}
    if not slot:
        for key, value in stack.items():
            if isinstance(value, dict) and as_int(value.get("count"), 0) > 0:
                inst_id, slot = str(key), value
                break
    if not slot or as_int(slot.get("count"), 0) < 1:
        return fail("campaign sweep ticket required")
    slot["count"] = as_int(slot.get("count"), 0) - 1
    call_obj.touch("consumable")
    instance = _instance(call_obj, stage_id)
    before = as_int(instance.get("maxKills"), 0)
    instance["maxKills"] = max(before, 400)
    call_obj.touch("campaignsV2")
    call_obj.extra.update(
        {
            "currentFeeBefore": before,
            "currentFeeAfter": instance["maxKills"],
            "unlockStages": [],
            "rewards": [],
            "unusualRewards": [],
            "additionalRewards": [],
            "diamondMaterialRewards": [],
            "furnitureRewards": [],
        }
    )
    return finish(call_obj, "campaignsV2", "consumable")


@router.post("/campaignV2/getBreakReward")
async def campaign_get_break_reward(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    stage_id = body_id(call_obj.body, "stageId", "stage")
    instance = _instance(call_obj, stage_id)
    ladder = _campaign_def(stage_id).get("breakLadders") or []
    indexes = call_obj.body.get("indexList")
    indexes = indexes if isinstance(indexes, list) else [as_int(call_obj.body.get("index"), 0)]
    max_kills = as_int(instance.get("maxKills"), 0)
    statuses = instance["rewardStatus"]
    gained = []
    for raw in indexes:
        index = as_int(raw, -1)
        if index < 0 or index >= len(statuses) or as_int(statuses[index], 0) == 1:
            continue
        if index >= len(ladder) or isinstance(ladder[index], dict) is False:
            continue
        row = as_dict(ladder[index])
        if max_kills < as_int(row.get("killCnt"), 0):
            continue
        statuses[index] = 1
        rewards = row.get("rewards") if isinstance(row.get("rewards"), list) else []
        gained.extend(apply_items(call_obj, rewards, f"campaign:{stage_id}:{index}"))
        state = _campaign_state(call_obj)
        state["campaignTotalFee"] = as_int(state.get("campaignTotalFee"), 0) + max(0, as_int(row.get("breakFeeAdd"), 0))
    call_obj.touch("campaignsV2")
    call_obj.extra["items"] = gained
    return finish(call_obj, "campaignsV2")


@router.post("/campaignV2/getExMissionReward")
async def campaign_get_ex_mission_reward(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    mission_id = body_id(call_obj.body, "id", "missionId")
    state = _campaign_state(call_obj)
    missions = ensure_dict(state, "missions")
    if not mission_id or as_int(missions.get(mission_id), 0) == 1:
        return fail("mission reward unavailable")
    missions[mission_id] = 1
    items = apply_items(call_obj, [{"id": "4001", "type": "GOLD", "count": 1000}], f"campaign-mission:{mission_id}")
    call_obj.extra["items"] = items
    return finish(call_obj, "campaignsV2")
