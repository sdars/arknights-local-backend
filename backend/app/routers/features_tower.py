"""Climb tower protocol implementation."""

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
    ensure_list,
    fail,
    finish,
    first_value,
    set_deleted,
    unique_append,
)


router = APIRouter()


def _tower(call_obj):
    return ensure_dict(call_obj.user, "tower")


def _current(call_obj):
    node = _tower(call_obj)
    return ensure_dict(node, "current")


def _outer(call_obj):
    node = _tower(call_obj)
    return ensure_dict(node, "outer")


def _table():
    return game_data.excel("climb_tower_table.json") or {}


def _tower_def(tower_id: str) -> dict:
    return as_dict(as_dict(_table().get("towers")).get(tower_id))


def _tower_levels(tower_id: str, hard: bool) -> list[str]:
    definition = _tower_def(tower_id)
    values = definition.get("hardLevels") if hard and isinstance(definition.get("hardLevels"), list) else definition.get("levels")
    return [str(value) for value in values or [] if value]


def _tower_record(call_obj, tower_id: str):
    towers = ensure_dict(_outer(call_obj), "towers")
    node = towers.get(tower_id)
    if not isinstance(node, dict):
        node = {"best": 0, "reward": [], "unlockHard": False, "hardBest": 0, "canSweep": False, "canSweepHard": False}
        towers[tower_id] = node
    return node


@router.post("/tower/createGame")
async def tower_create_game(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    tower_id = body_id(call_obj.body, "tower", "towerId")
    hard = bool(as_int(call_obj.body.get("isHard"), 0))
    if not _tower_def(tower_id):
        return fail(f"unknown tower: {tower_id}")
    levels = _tower_levels(tower_id, hard)
    if not levels:
        return fail("tower has no levels")
    current = _current(call_obj)
    current["status"] = {
        "state": "STANDBY",
        "tower": tower_id,
        "coord": 0,
        "tactical": {name: "" for name in ("SNIPER", "SUPPORT", "WARRIOR", "CASTER", "PIONEER", "MEDIC", "TANK", "SPECIAL")},
        "strategy": "NONE",
        "start": now_ts(),
        "isHard": hard,
    }
    current["layer"] = [{"id": level_id, "try": 0, "pass": False} for level_id in levels]
    current["cards"] = {}
    current["godCard"] = {"id": "", "subGodCardId": ""}
    current["halftime"] = {"count": 0, "candidate": [], "canGiveUp": False}
    current["trap"] = []
    call_obj.touch("tower")
    return finish(call_obj, "tower")


@router.post("/tower/battleStart")
async def tower_battle_start(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    current = _current(call_obj)
    status = ensure_dict(current, "status")
    if status.get("state") in {"NONE", ""} or not status.get("tower"):
        return fail("tower game not created")
    layers = ensure_list(current, "layer")
    stage_id = body_id(call_obj.body, "stageId", "levelId")
    layer = next((row for row in layers if isinstance(row, dict) and row.get("id") == stage_id), None)
    if layer is None and layers:
        layer = next((row for row in layers if isinstance(row, dict) and not row.get("pass")), layers[0])
    if not isinstance(layer, dict):
        return fail("no tower layer available")
    layer["try"] = as_int(layer.get("try"), 0) + 1
    status["state"] = "BATTLE"
    status["currentLevel"] = layer.get("id")
    status["battleId"] = battle_id("tower", call_obj.account)
    call_obj.touch("tower")
    result = finish(call_obj, "tower")
    result["battleId"] = status["battleId"]
    result["stageId"] = layer.get("id")
    return result


@router.post("/tower/battleFinish")
async def tower_battle_finish(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    current = _current(call_obj)
    status = ensure_dict(current, "status")
    layers = ensure_list(current, "layer")
    stage_id = body_id(call_obj.body, "stageId", "levelId", default=str(status.get("currentLevel") or ""))
    layer = next((row for row in layers if isinstance(row, dict) and row.get("id") == stage_id), None)
    if layer is None:
        layer = next((row for row in layers if isinstance(row, dict) and not row.get("pass")), None)
    if not isinstance(layer, dict):
        return fail("no tower layer available")
    complete = as_int(call_obj.body.get("completeState"), 3)
    if complete >= 2 or as_int(call_obj.body.get("pass"), 1):
        layer["pass"] = True
    status["state"] = "STANDBY"
    tower_id = str(status.get("tower") or "")
    record = _tower_record(call_obj, tower_id)
    passed = sum(1 for row in layers if isinstance(row, dict) and row.get("pass"))
    hard = bool(status.get("isHard"))
    key = "hardBest" if hard else "best"
    record[key] = max(as_int(record.get(key), 0), passed)
    record["canSweep"] = record["best"] >= max(1, len(layers))
    record["canSweepHard"] = record["hardBest"] >= max(1, len(_tower_levels(tower_id, True)))
    call_obj.touch("tower")
    result = finish(call_obj, "tower")
    result["result"] = 0
    result["items"] = []
    result["unlockStages"] = []
    return result


@router.post("/tower/initGame")
async def tower_init_game(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    current = _current(call_obj)
    status = ensure_dict(current, "status")
    status["state"] = "STANDBY"
    status["tactical"] = as_dict(call_obj.body.get("tactical")) or status.get("tactical", {})
    status["strategy"] = str(call_obj.body.get("strategy") or "NONE")
    status["start"] = now_ts()
    return finish(call_obj, "tower")


@router.post("/tower/initGodCard")
async def tower_init_god_card(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    god_id = body_id(call_obj.body, "godCardId", "id")
    if god_id and god_id not in as_dict(_table().get("mainCards")):
        return fail(f"unknown god card: {god_id}")
    current = _current(call_obj)
    current["godCard"] = {"id": god_id, "subGodCardId": ""}
    ensure_dict(current, "status")["state"] = "STANDBY"
    return finish(call_obj, "tower")


@router.post("/tower/initCard")
async def tower_init_card(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    cards = ensure_dict(_current(call_obj), "cards")
    raw = first_value(call_obj.body, "cards", "cardIds", "cardList", default=[])
    if isinstance(raw, dict):
        cards.update(raw)
    elif isinstance(raw, list):
        for card in raw:
            if isinstance(card, dict) and card.get("id"):
                cards[str(card["id"])] = card
            elif card:
                cards[str(card)] = {"id": str(card)}
    return finish(call_obj, "tower")


@router.post("/tower/recruit")
async def tower_recruit(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    char_id = body_id(call_obj.body, "charId", "id")
    if not char_id:
        return fail("charId required")
    current = _current(call_obj)
    halftime = ensure_dict(current, "halftime")
    if as_int(halftime.get("count"), 0) <= 0 and call_obj.body.get("giveUp"):
        return fail("no halftime recruit available")
    cards = ensure_dict(current, "cards")
    cards[char_id] = {"id": char_id, "charId": char_id, "level": 1}
    if as_int(halftime.get("count"), 0) > 0:
        halftime["count"] = as_int(halftime.get("count"), 0) - 1
    call_obj.touch("tower")
    return finish(call_obj, "tower")


def _level_reward_items(call_obj, level_id: str) -> list[dict]:
    definition = as_dict(as_dict(_table().get("levels")).get(level_id))
    drop = as_dict(as_dict(definition.get("dropInfo")).get("displayDropInfo"))
    rewards = []
    for item_id, value in drop.items():
        node = as_dict(value)
        count = max(as_int(node.get("minCount"), 0), as_int(node.get("maxCount"), 0))
        if count > 0:
            rewards.append({"id": str(item_id), "type": "MATERIAL", "count": count})
    return apply_items(call_obj, rewards, f"tower-layer:{level_id}") if rewards else []


@router.post("/tower/layerReward")
async def tower_layer_reward(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    tower_id = body_id(call_obj.body, "tower", "towerId", default=str(ensure_dict(_current(call_obj), "status").get("tower") or ""))
    layers = call_obj.body.get("layers")
    layers = layers if isinstance(layers, list) else []
    record = _tower_record(call_obj, tower_id)
    rewards = ensure_list(record, "reward")
    level_ids = _tower_levels(tower_id, bool(ensure_dict(_current(call_obj), "status").get("isHard")))
    gained = []
    for raw in layers:
        index = as_int(raw, 0)
        if index in rewards or index < 1 or index > len(level_ids):
            continue
        rewards.append(index)
        gained.extend(_level_reward_items(call_obj, level_ids[index - 1]))
        record["best"] = max(as_int(record.get("best"), 0), index)
    call_obj.touch("tower")
    call_obj.extra["items"] = gained
    return finish(call_obj, "tower")


@router.post("/tower/sweepGame")
async def tower_sweep_game(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    tower_id = body_id(call_obj.body, "tower", "towerId")
    hard = bool(as_int(call_obj.body.get("isHard"), 0))
    record = _tower_record(call_obj, tower_id)
    best = as_int(record.get("hardBest") if hard else record.get("best"), 0)
    if best <= 0:
        return fail("tower has not been cleared")
    item_id = body_id(call_obj.body, "itemId", default="EXTERMINATION_AGENT")
    inst_ids = call_obj.body.get("instIds")
    inst_id = str((inst_ids or ["0"])[0]) if isinstance(inst_ids, list) and inst_ids else "0"
    stack = as_dict(ensure_dict(call_obj.user, "consumable").get(item_id))
    slot = as_dict(stack.get(inst_id)) if stack else {}
    if not slot:
        for key, value in stack.items():
            if isinstance(value, dict) and as_int(value.get("count"), 0) > 0:
                inst_id, slot = str(key), value
                break
    if not slot or as_int(slot.get("count"), 0) < 2:
        return fail("not enough sweep tickets")
    slot["count"] = as_int(slot.get("count"), 0) - 2
    call_obj.touch("consumable")
    items = []
    for level_id in _tower_levels(tower_id, hard)[:best]:
        items.extend(_level_reward_items(call_obj, level_id))
    call_obj.extra["items"] = items
    return finish(call_obj, "tower", "consumable")


@router.post("/tower/seasonMissonsAward")
async def tower_season_missions_award(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    ids = call_obj.body.get("missionIds")
    ids = ids if isinstance(ids, list) else []
    season = ensure_dict(_tower(call_obj), "season")
    missions = ensure_dict(season, "missions")
    definitions = as_dict(_table().get("missionData"))
    items = []
    for mission_id in ids:
        node = ensure_dict(missions, str(mission_id))
        if node.get("hasRecv"):
            continue
        if as_int(node.get("value"), 0) < as_int(node.get("target"), 1):
            continue
        node["hasRecv"] = True
        rewards = as_dict(definitions.get(str(mission_id))).get("rewards")
        if isinstance(rewards, list):
            items.extend(apply_items(call_obj, rewards, f"tower-season:{mission_id}"))
    call_obj.extra["items"] = items
    return finish(call_obj, "tower")


@router.post("/tower/chooseSubGodCard")
async def tower_choose_sub_god_card(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    sub_id = body_id(call_obj.body, "subGodCardId", "id")
    current = _current(call_obj)
    god = ensure_dict(current, "godCard")
    god["subGodCardId"] = sub_id
    god_id = str(god.get("id") or "")
    if god_id and sub_id:
        picked = ensure_dict(_outer(call_obj), "pickedGodCard")
        unique_append(ensure_list(picked, god_id), sub_id)
    ensure_dict(current, "status")["state"] = "STANDBY"
    return finish(call_obj, "tower")


@router.post("/tower/settleGame")
async def tower_settle_game(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    current = _current(call_obj)
    status = ensure_dict(current, "status")
    layers = ensure_list(current, "layer")
    passed = all(bool(as_dict(row).get("pass")) for row in layers if isinstance(row, dict))
    tower_id = str(status.get("tower") or "")
    if tower_id and passed:
        record = _tower_record(call_obj, tower_id)
        record["best"] = max(as_int(record.get("best"), 0), len(layers))
        record["canSweep"] = True
    old_cards = list(as_dict(current.get("cards")).keys())
    if old_cards:
        set_deleted(call_obj, "tower", {"current": {"cards": old_cards}})
    current.clear()
    current.update(
        {
            "godCard": {"subGodCardId": "", "id": ""},
            "cards": {},
            "halftime": {"canGiveUp": False, "candidate": [], "count": 0},
            "trap": [],
            "layer": [],
            "status": {
                "coord": 0,
                "isHard": False,
                "start": 0,
                "state": "NONE",
                "tactical": {name: "" for name in ("SNIPER", "SUPPORT", "WARRIOR", "CASTER", "PIONEER", "MEDIC", "TANK", "SPECIAL")},
                "strategy": "OPTIMIZE",
                "tower": "",
            },
        }
    )
    return finish(call_obj, "tower")
