"""Reclamation Algorithm / sandbox permanent domains (V2 and V3)."""

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
    set_deleted,
    unique_append,
)


router = APIRouter()


def sandbox_route(path: str):
    def decorator(fn: Callable):
        router.add_api_route(path, fn, methods=["POST"], name=fn.__name__)
        return fn

    return decorator


def _root(call_obj):
    return ensure_dict(call_obj.user, "sandboxPerm")


def _topic(call_obj, mode: str) -> tuple[dict, dict, str]:
    root = _root(call_obj)
    templates = ensure_dict(root, "template")
    mode_map = ensure_dict(templates, "SANDBOX_V3" if mode == "V3" else "SANDBOX_V2")
    requested = body_id(call_obj.body, "topic", "topicId", "sandboxId")
    topic = requested or str(root.get("topic") or "")
    if not topic or topic not in mode_map:
        topic = next(iter(mode_map), "sandbox_2" if mode == "V3" else "sandbox_1")
    run = ensure_dict(mode_map, topic)
    if mode == "V3":
        run.setdefault("game", {"modeId": "sandbox_2_mode_normal", "state": "NONE", "day": 0})
        run.setdefault("base", {"score": 0, "level": 1, "building": {}, "production": {}, "shop": {"good": {}}})
        run.setdefault("inventory", {"coin": {}, "trap": {}, "cookbook": []})
        run.setdefault("map", {"node": {}, "zone": {}})
        run.setdefault("squad", {})
        run.setdefault("tech", {"unlock": [], "token": 0})
        run.setdefault("dailyRecruit", {"recruited": [], "pool": []})
        ensure_dict(root, "summary").setdefault("SANDBOX_V3", {}).setdefault(topic, {"inCurrent": True, "baseLv": run["base"].get("level", 1)})
    else:
        run.setdefault("game", {"modeId": "sandbox_1_mode_normal", "state": "NONE", "day": 0})
        run.setdefault("base", {"score": 0, "level": 1, "building": {}, "production": {}, "shop": {"good": {}}})
        run.setdefault("inventory", {"coin": {}, "trap": {}, "food": {}})
        run.setdefault("map", {"node": {}, "zone": {}})
        run.setdefault("squad", {})
        run.setdefault("tech", {"unlock": [], "token": 0})
        run.setdefault("challenge", {"active": False, "reward": {}})
        run.setdefault("rift", {})
        run.setdefault("racing", {})
        ensure_dict(root, "summary").setdefault("SANDBOX_V2", {}).setdefault(topic, {"seasonType": 0, "inChallenge": False, "day": run["game"].get("day", 0)})
    return root, run, topic


def _create(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    run["game"] = {
        "modeId": body_id(call_obj.body, "modeId", "mode", default=("sandbox_2_mode_normal" if mode == "V3" else "sandbox_1_mode_normal")),
        "state": "RUNNING",
        "day": 1,
        "startTs": now_ts(),
        "seed": body_id(call_obj.body, "seed", default=str(now_ts())),
    }
    run["base"]["level"] = 1
    run["base"]["score"] = 0
    run["tech"]["token"] = max(as_int(run["tech"].get("token"), 0), 5)
    run.setdefault("settled", False)
    root["topic"] = topic
    root["loadTs"] = now_ts()
    call_obj.touch("sandboxPerm")
    result = finish(call_obj, "sandboxPerm")
    result["game"] = run["game"]
    result["topic"] = topic
    return result


def _load(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    root["topic"] = topic
    root["loadTs"] = now_ts()
    return finish(call_obj, "sandboxPerm")


def _battle_start(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    game = ensure_dict(run, "game")
    game["state"] = "BATTLE"
    game["battleId"] = battle_id(f"sandbox-{mode.lower()}", call_obj.account)
    game["battleStage"] = body_id(call_obj.body, "stageId", "nodeId", "missionId")
    game["battleTs"] = now_ts()
    call_obj.touch("sandboxPerm")
    result = finish(call_obj, "sandboxPerm")
    result["battleId"] = game["battleId"]
    return result


def _battle_finish(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    game = ensure_dict(run, "game")
    game["state"] = "RUNNING"
    game["lastBattleResult"] = "WIN" if as_int(call_obj.body.get("completeState"), 3) >= 2 else "LOSE"
    score = max(0, as_int(call_obj.body.get("score"), as_int(game.get("score"), 0)))
    game["score"] = score
    run["base"]["score"] = max(as_int(run["base"].get("score"), 0), score)
    node_id = body_id(call_obj.body, "nodeId", "stageId")
    if node_id:
        ensure_dict(ensure_dict(run, "map"), "node")[node_id] = 1
    call_obj.touch("sandboxPerm")
    result = finish(call_obj, "sandboxPerm")
    result["items"] = []
    return result


def _save_build(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    base = ensure_dict(run, "base")
    payload = first_value(call_obj.body, "build", "building", "data", "slots", default={})
    base["building"] = payload if isinstance(payload, (dict, list)) else {}
    base["homeTs"] = now_ts()
    return finish(call_obj, "sandboxPerm")


def _upgrade(call_obj, mode: str, key: str):
    root, run, topic = _topic(call_obj, mode)
    base = ensure_dict(run, "base")
    current = as_int(base.get("level"), 1)
    base["level"] = current + 1
    base[key] = {"level": base["level"], "ts": now_ts()}
    ensure_dict(root, "summary").setdefault("SANDBOX_V3" if mode == "V3" else "SANDBOX_V2", {}).setdefault(topic, {})["baseLv"] = base["level"]
    return finish(call_obj, "sandboxPerm")


def _food(call_obj, mode: str, consume: bool):
    root, run, topic = _topic(call_obj, mode)
    inventory = ensure_dict(run, "inventory")
    foods = ensure_dict(inventory, "food")
    food_id = body_id(call_obj.body, "foodId", "itemId", "id")
    if not food_id:
        return fail("foodId required")
    if consume:
        if as_int(foods.get(food_id), 0) <= 0:
            return fail("food not owned")
        foods[food_id] = as_int(foods.get(food_id), 0) - 1
        ensure_dict(run, "game")["lastFood"] = food_id
    else:
        foods[food_id] = as_int(foods.get(food_id), 0) + max(1, as_int(call_obj.body.get("count"), 1))
        if food_id not in ensure_list(inventory, "cookbook"):
            inventory["cookbook"].append(food_id)
    call_obj.touch("sandboxPerm")
    return finish(call_obj, "sandboxPerm")


def _event_choice(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    event_id = body_id(call_obj.body, "eventId", "nodeId", "id")
    option = as_int(first_value(call_obj.body, "option", "choice", "index", default=0), 0)
    if not event_id:
        return fail("eventId required")
    events = ensure_dict(run, "events")
    ensure_list(ensure_dict(events, event_id), "choices").append(option)
    ensure_dict(run, "game")["lastEvent"] = event_id
    return finish(call_obj, "sandboxPerm")


def _next_day(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    game = ensure_dict(run, "game")
    game["day"] = as_int(game.get("day"), 0) + 1
    game["state"] = "RUNNING"
    base = ensure_dict(run, "base")
    base["production"] = ensure_dict(base, "production")
    base["production"]["lastHarvestDay"] = game["day"]
    root["loadTs"] = now_ts()
    return finish(call_obj, "sandboxPerm")


def _shop_buy(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    base = ensure_dict(run, "base")
    shop = ensure_dict(base, "shop")
    goods = ensure_dict(shop, "good")
    good_id = body_id(call_obj.body, "goodId", "itemId", "id")
    if not good_id:
        return fail("goodId required")
    good = ensure_dict(goods, good_id)
    count = max(1, as_int(call_obj.body.get("count"), 1))
    price = max(0, as_int(good.get("price"), as_int(call_obj.body.get("price"), 0)))
    coin = ensure_dict(ensure_dict(run, "inventory"), "coin")
    currency = body_id(call_obj.body, "coinId", "currency", default="sandbox_coin")
    if price and as_int(coin.get(currency), 0) < price * count:
        return fail("not enough sandbox coin")
    if price:
        coin[currency] = as_int(coin.get(currency), 0) - price * count
    good["bought"] = as_int(good.get("bought"), 0) + count
    items = ensure_dict(run, "items")
    items[good_id] = as_int(items.get(good_id), 0) + count
    return finish(call_obj, "sandboxPerm")


def _shop_sell(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    good_id = body_id(call_obj.body, "goodId", "itemId", "id")
    count = max(1, as_int(call_obj.body.get("count"), 1))
    items = ensure_dict(run, "items")
    if as_int(items.get(good_id), 0) < count:
        return fail("item not owned")
    items[good_id] = as_int(items.get(good_id), 0) - count
    coin = ensure_dict(ensure_dict(run, "inventory"), "coin")
    currency = body_id(call_obj.body, "coinId", "currency", default="sandbox_coin")
    coin[currency] = as_int(coin.get(currency), 0) + max(1, as_int(call_obj.body.get("price"), 1)) * count
    return finish(call_obj, "sandboxPerm")


def _shop_refresh(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    shop = ensure_dict(ensure_dict(run, "base"), "shop")
    shop["refreshTs"] = now_ts()
    shop["refreshCount"] = as_int(shop.get("refreshCount"), 0) + 1
    return finish(call_obj, "sandboxPerm")


def _unlock_tech(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    tech = ensure_dict(run, "tech")
    tech_id = body_id(call_obj.body, "techId", "id")
    if not tech_id:
        return fail("techId required")
    unlocked = ensure_list(tech, "unlock")
    if tech_id not in unlocked:
        cost = max(0, as_int(call_obj.body.get("cost"), 1))
        if as_int(tech.get("token"), 0) < cost:
            return fail("not enough tech token")
        tech["token"] = as_int(tech.get("token"), 0) - cost
        unlocked.append(tech_id)
    return finish(call_obj, "sandboxPerm")


def _set_squad(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    squad = first_value(call_obj.body, "squad", "slots", "chars", default=[])
    run["squad"] = squad if isinstance(squad, dict) else {"slots": squad if isinstance(squad, list) else []}
    return finish(call_obj, "sandboxPerm")


def _set_supply(call_obj, mode: str, remove: bool = False):
    root, run, topic = _topic(call_obj, mode)
    supply_id = body_id(call_obj.body, "supplyId", "itemId", "id")
    if not supply_id:
        return fail("supplyId required")
    supply = ensure_dict(run, "supply")
    if remove:
        supply.pop(supply_id, None)
    else:
        supply[supply_id] = call_obj.body.get("supply", call_obj.body.get("data", {}))
    return finish(call_obj, "sandboxPerm")


def _challenge(call_obj, mode: str, action: str):
    root, run, topic = _topic(call_obj, mode)
    challenge = ensure_dict(run, "challenge")
    if action == "enter":
        challenge["active"] = True
        challenge["challengeId"] = body_id(call_obj.body, "challengeId", "id")
        challenge["startTs"] = now_ts()
    elif action == "exit":
        challenge["active"] = False
        challenge["exitTs"] = now_ts()
    elif action == "settle":
        challenge["active"] = False
        challenge["settled"] = True
        challenge["score"] = max(0, as_int(call_obj.body.get("score"), 0))
        challenge["settleTs"] = now_ts()
    elif action == "reward":
        reward_id = body_id(call_obj.body, "rewardId", "id", default="challenge")
        claimed = ensure_dict(challenge, "reward")
        if claimed.get(reward_id):
            return fail("challenge reward already claimed")
        claimed[reward_id] = 1
        call_obj.extra["items"] = apply_items(call_obj, [{"id": "4001", "type": "GOLD", "count": 1500}], "sandbox-challenge")
    return finish(call_obj, "sandboxPerm")


def _rift(call_obj, mode: str, action: str):
    root, run, topic = _topic(call_obj, mode)
    rift = ensure_dict(run, "rift")
    if action == "create":
        rift.update(
            {
                "id": body_id(call_obj.body, "riftId", "id", default=f"rift_{now_ts()}"),
                "difficulty": as_int(call_obj.body.get("difficulty"), 0),
                "state": "OPEN",
                "openTs": now_ts(),
            }
        )
    elif action == "close":
        rift["state"] = "CLOSED"
        rift["closeTs"] = now_ts()
    elif action == "difficulty":
        rift["difficulty"] = as_int(call_obj.body.get("difficulty"), 0)
    elif action == "team":
        rift["team"] = first_value(call_obj.body, "team", "squad", "slots", default=[])
    elif action == "settle":
        rift["state"] = "SETTLED"
        rift["score"] = max(0, as_int(call_obj.body.get("score"), 0))
        rift["settleTs"] = now_ts()
        call_obj.extra["items"] = apply_items(call_obj, [{"id": "4001", "type": "GOLD", "count": 1000}], "sandbox-rift")
    return finish(call_obj, "sandboxPerm")


def _racing(call_obj, mode: str, action: str):
    root, run, topic = _topic(call_obj, mode)
    racing = ensure_dict(run, "racing")
    if action == "register":
        racing["registered"] = True
        racing["registerTs"] = now_ts()
    elif action == "release":
        racing["registered"] = False
        racing["releaseTs"] = now_ts()
    elif action == "saveMark":
        racing["mark"] = call_obj.body.get("mark") or call_obj.body.get("data") or {}
    elif action == "learnTalent":
        talent_id = body_id(call_obj.body, "talentId", "id")
        if not talent_id:
            return fail("talentId required")
        unique_append(ensure_list(racing, "talents"), talent_id)
    elif action == "battleStart":
        racing["state"] = "BATTLE"
        racing["battleId"] = battle_id("sandbox-racing", call_obj.account)
    elif action == "battleFinish":
        racing["state"] = "FINISH"
        racing["score"] = max(0, as_int(call_obj.body.get("score"), 0))
        racing["finishTs"] = now_ts()
    return finish(call_obj, "sandboxPerm")


def _settle(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    game = ensure_dict(run, "game")
    game["state"] = "SETTLED"
    game["settleTs"] = now_ts()
    run["settled"] = True
    reward = 1000 + max(0, as_int(run["base"].get("score"), 0)) // 100
    items = apply_items(call_obj, [{"id": "4001", "type": "GOLD", "count": reward}], f"sandbox-settle:{mode}")
    call_obj.extra["items"] = items
    call_obj.extra["score"] = as_int(run["base"].get("score"), 0)
    return finish(call_obj, "sandboxPerm")


def _switch_mode(call_obj, mode: str):
    root, run, topic = _topic(call_obj, mode)
    target = body_id(call_obj.body, "modeId", "mode", default="normal")
    run["game"]["modeId"] = target
    run["game"]["modeChangedTs"] = now_ts()
    return finish(call_obj, "sandboxPerm")


# ── Top-level topic management ───────────────────────────────────────────────


@sandbox_route("/sandboxPerm/changeTopic")
async def sandbox_change_topic(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root = _root(call_obj)
    topic = body_id(call_obj.body, "topic", "topicId", "sandboxId")
    if not topic:
        return fail("topic required")
    root["topic"] = topic
    root["loadTs"] = now_ts()
    return finish(call_obj, "sandboxPerm")


@sandbox_route("/sandboxPerm/pinTopic")
async def sandbox_pin_topic(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root = _root(call_obj)
    raw = first_value(call_obj.body, "pin", "topic", "topicId", default="")
    root["pin"] = str(raw or "")
    return finish(call_obj, "sandboxPerm")


# ── V2 ───────────────────────────────────────────────────────────────────────


@sandbox_route("/sandboxPerm/sandboxV2/createGame")
async def sandbox_v2_create_game(request: Request):
    call_obj = await call(request)
    return _create(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/load")
async def sandbox_v2_load(request: Request):
    call_obj = await call(request)
    return _load(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/guideLoad")
async def sandbox_v2_guide_load(request: Request):
    call_obj = await call(request)
    return _load(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/battleStart")
async def sandbox_v2_battle_start(request: Request):
    call_obj = await call(request)
    return _battle_start(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/battleFinish")
async def sandbox_v2_battle_finish(request: Request):
    call_obj = await call(request)
    return _battle_finish(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/monthBattleStart")
async def sandbox_v2_month_battle_start(request: Request):
    call_obj = await call(request)
    return _battle_start(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/monthBattleFinish")
async def sandbox_v2_month_battle_finish(request: Request):
    call_obj = await call(request)
    return _battle_finish(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/build")
async def sandbox_v2_build(request: Request):
    call_obj = await call(request)
    return _save_build(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/homeBuildSave")
async def sandbox_v2_home_build_save(request: Request):
    call_obj = await call(request)
    return _save_build(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/baseUpgrade")
async def sandbox_v2_base_upgrade(request: Request):
    call_obj = await call(request)
    return _upgrade(call_obj, "V2", "base") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/alchemy")
async def sandbox_v2_alchemy(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, run, topic = _topic(call_obj, "V2")
    recipe = body_id(call_obj.body, "recipeId", "formulaId", "id")
    if not recipe:
        return fail("recipeId required")
    outputs = ensure_dict(run, "alchemy")
    outputs[recipe] = as_int(outputs.get(recipe), 0) + 1
    return finish(call_obj, "sandboxPerm")


@sandbox_route("/sandboxPerm/sandboxV2/cook")
async def sandbox_v2_cook(request: Request):
    call_obj = await call(request)
    return _food(call_obj, "V2", consume=False) if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/eatFood")
async def sandbox_v2_eat_food(request: Request):
    call_obj = await call(request)
    return _food(call_obj, "V2", consume=True) if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/discardAp")
async def sandbox_v2_discard_ap(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, run, topic = _topic(call_obj, "V2")
    game = ensure_dict(run, "game")
    game["ap"] = max(0, as_int(game.get("ap"), 0) - max(0, as_int(call_obj.body.get("ap"), 1)))
    return finish(call_obj, "sandboxPerm")


@sandbox_route("/sandboxPerm/sandboxV2/eventChoice")
async def sandbox_v2_event_choice(request: Request):
    call_obj = await call(request)
    return _event_choice(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/enterChallenge")
async def sandbox_v2_enter_challenge(request: Request):
    call_obj = await call(request)
    return _challenge(call_obj, "V2", "enter") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/exitChallenge")
async def sandbox_v2_exit_challenge(request: Request):
    call_obj = await call(request)
    return _challenge(call_obj, "V2", "exit") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/settleChallenge")
async def sandbox_v2_settle_challenge(request: Request):
    call_obj = await call(request)
    return _challenge(call_obj, "V2", "settle") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/getChallengeReward")
async def sandbox_v2_get_challenge_reward(request: Request):
    call_obj = await call(request)
    return _challenge(call_obj, "V2", "reward") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/extract")
async def sandbox_v2_extract(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, run, topic = _topic(call_obj, "V2")
    extracted = ensure_dict(run, "extract")
    source = body_id(call_obj.body, "nodeId", "sourceId", "id", default="default")
    extracted[source] = now_ts()
    return finish(call_obj, "sandboxPerm")


@sandbox_route("/sandboxPerm/sandboxV2/nextDay")
async def sandbox_v2_next_day(request: Request):
    call_obj = await call(request)
    return _next_day(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/settleDay")
async def sandbox_v2_settle_day(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, run, topic = _topic(call_obj, "V2")
    game = ensure_dict(run, "game")
    game["daySettled"] = as_int(game.get("day"), 0)
    game["daySettleTs"] = now_ts()
    return finish(call_obj, "sandboxPerm")


@sandbox_route("/sandboxPerm/sandboxV2/settleGame")
async def sandbox_v2_settle_game(request: Request):
    call_obj = await call(request)
    return _settle(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/shopBuy")
async def sandbox_v2_shop_buy(request: Request):
    call_obj = await call(request)
    return _shop_buy(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/startMission")
async def sandbox_v2_start_mission(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, run, topic = _topic(call_obj, "V2")
    mission_id = body_id(call_obj.body, "missionId", "id")
    if not mission_id:
        return fail("missionId required")
    ensure_dict(run, "game")["missionId"] = mission_id
    ensure_dict(run, "game")["missionStartTs"] = now_ts()
    return finish(call_obj, "sandboxPerm")


@sandbox_route("/sandboxPerm/sandboxV2/switchMode")
async def sandbox_v2_switch_mode(request: Request):
    call_obj = await call(request)
    return _switch_mode(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/unlockTech")
async def sandbox_v2_unlock_tech(request: Request):
    call_obj = await call(request)
    return _unlock_tech(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/setSquad")
async def sandbox_v2_set_squad(request: Request):
    call_obj = await call(request)
    return _set_squad(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/setSupply")
async def sandbox_v2_set_supply(request: Request):
    call_obj = await call(request)
    return _set_supply(call_obj, "V2") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/removeSupply")
async def sandbox_v2_remove_supply(request: Request):
    call_obj = await call(request)
    return _set_supply(call_obj, "V2", remove=True) if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/riftCreate")
async def sandbox_v2_rift_create(request: Request):
    call_obj = await call(request)
    return _rift(call_obj, "V2", "create") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/riftClose")
async def sandbox_v2_rift_close(request: Request):
    call_obj = await call(request)
    return _rift(call_obj, "V2", "close") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/riftSetDifficulty")
async def sandbox_v2_rift_difficulty(request: Request):
    call_obj = await call(request)
    return _rift(call_obj, "V2", "difficulty") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/riftSetTeam")
async def sandbox_v2_rift_team(request: Request):
    call_obj = await call(request)
    return _rift(call_obj, "V2", "team") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/riftSettle")
async def sandbox_v2_rift_settle(request: Request):
    call_obj = await call(request)
    return _rift(call_obj, "V2", "settle") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/racing/register")
async def sandbox_v2_racing_register(request: Request):
    call_obj = await call(request)
    return _racing(call_obj, "V2", "register") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/racing/release")
async def sandbox_v2_racing_release(request: Request):
    call_obj = await call(request)
    return _racing(call_obj, "V2", "release") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/racing/saveMark")
async def sandbox_v2_racing_save_mark(request: Request):
    call_obj = await call(request)
    return _racing(call_obj, "V2", "saveMark") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/racing/learnTalent")
async def sandbox_v2_racing_learn_talent(request: Request):
    call_obj = await call(request)
    return _racing(call_obj, "V2", "learnTalent") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/racing/battleStart")
async def sandbox_v2_racing_battle_start(request: Request):
    call_obj = await call(request)
    return _racing(call_obj, "V2", "battleStart") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV2/racing/battleFinish")
async def sandbox_v2_racing_battle_finish(request: Request):
    call_obj = await call(request)
    return _racing(call_obj, "V2", "battleFinish") if call_obj else auth_error()


# ── V3 ───────────────────────────────────────────────────────────────────────


@sandbox_route("/sandboxPerm/sandboxV3/createGame")
async def sandbox_v3_create_game(request: Request):
    call_obj = await call(request)
    return _create(call_obj, "V3") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/battleStart")
async def sandbox_v3_battle_start(request: Request):
    call_obj = await call(request)
    return _battle_start(call_obj, "V3") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/battleFinish")
async def sandbox_v3_battle_finish(request: Request):
    call_obj = await call(request)
    return _battle_finish(call_obj, "V3") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/changeDefend")
async def sandbox_v3_change_defend(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, run, topic = _topic(call_obj, "V3")
    zone_id = body_id(call_obj.body, "zoneId", "id")
    node = ensure_dict(ensure_dict(ensure_dict(run, "map"), "zone"), zone_id)
    node["defend"] = {
        "main": as_int(call_obj.body.get("main"), -1),
        "sub": call_obj.body.get("sub") if isinstance(call_obj.body.get("sub"), list) else [],
    }
    return finish(call_obj, "sandboxPerm")


@sandbox_route("/sandboxPerm/sandboxV3/chooseBand")
async def sandbox_v3_choose_band(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, run, topic = _topic(call_obj, "V3")
    band_id = body_id(call_obj.body, "bandId", "id")
    if not band_id:
        return fail("bandId required")
    bands = ensure_dict(run, "band")
    node = ensure_dict(bands, band_id)
    node["level"] = max(1, as_int(node.get("level"), 1))
    run["game"]["bandId"] = band_id
    return finish(call_obj, "sandboxPerm")


@sandbox_route("/sandboxPerm/sandboxV3/eatFood")
async def sandbox_v3_eat_food(request: Request):
    call_obj = await call(request)
    return _food(call_obj, "V3", consume=True) if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/eventChoice")
async def sandbox_v3_event_choice(request: Request):
    call_obj = await call(request)
    return _event_choice(call_obj, "V3") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/getDailyRecruitList")
async def sandbox_v3_get_daily_recruit_list(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, run, topic = _topic(call_obj, "V3")
    chars = ensure_dict(ensure_dict(call_obj.user, "troop"), "chars")
    pool = []
    for char in chars.values():
        if not isinstance(char, dict):
            continue
        pool.append(
            {
                "charId": char.get("charId"),
                "level": as_int(char.get("level"), 1),
                "evolvePhase": as_int(char.get("evolvePhase"), 0),
                "potentialRank": as_int(char.get("potentialRank"), 0),
                "skinId": char.get("skin"),
            }
        )
    ensure_dict(run, "dailyRecruit")["pool"] = pool
    return {"result": 0, "recruitList": pool, "playerDataDelta": {"deleted": {}, "modified": {"sandboxPerm": root}}}


@sandbox_route("/sandboxPerm/sandboxV3/dailyRecruit")
async def sandbox_v3_daily_recruit(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, run, topic = _topic(call_obj, "V3")
    char_id = body_id(call_obj.body, "charId", "id")
    daily = ensure_dict(run, "dailyRecruit")
    if not char_id:
        return fail("charId required")
    unique_append(ensure_list(daily, "recruited"), char_id)
    ensure_dict(run, "squad")
    run["squad"].setdefault("chars", [])
    unique_append(run["squad"]["chars"], char_id)
    return finish(call_obj, "sandboxPerm")


@sandbox_route("/sandboxPerm/sandboxV3/initRecruit")
async def sandbox_v3_init_recruit(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, run, topic = _topic(call_obj, "V3")
    daily = ensure_dict(run, "dailyRecruit")
    daily["initTs"] = now_ts()
    daily["slots"] = first_value(call_obj.body, "slots", "chars", default=[])
    return finish(call_obj, "sandboxPerm")


@sandbox_route("/sandboxPerm/sandboxV3/giveUpGame")
async def sandbox_v3_give_up(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, run, topic = _topic(call_obj, "V3")
    game = ensure_dict(run, "game")
    game["state"] = "GAVE_UP"
    game["giveUpTs"] = now_ts()
    run["settled"] = True
    return finish(call_obj, "sandboxPerm")


@sandbox_route("/sandboxPerm/sandboxV3/homeEnter")
async def sandbox_v3_home_enter(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, run, topic = _topic(call_obj, "V3")
    ensure_dict(run, "home")["enteredTs"] = now_ts()
    return finish(call_obj, "sandboxPerm")


@sandbox_route("/sandboxPerm/sandboxV3/homeSave")
async def sandbox_v3_home_save(request: Request):
    call_obj = await call(request)
    return _save_build(call_obj, "V3") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/homeShopBuy")
async def sandbox_v3_home_shop_buy(request: Request):
    call_obj = await call(request)
    return _shop_buy(call_obj, "V3") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/homeShopSell")
async def sandbox_v3_home_shop_sell(request: Request):
    call_obj = await call(request)
    return _shop_sell(call_obj, "V3") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/homeUpgrade")
async def sandbox_v3_home_upgrade(request: Request):
    call_obj = await call(request)
    return _upgrade(call_obj, "V3", "home") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/nextDay")
async def sandbox_v3_next_day(request: Request):
    call_obj = await call(request)
    return _next_day(call_obj, "V3") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/settleGame")
async def sandbox_v3_settle_game(request: Request):
    call_obj = await call(request)
    return _settle(call_obj, "V3") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/shopBuy")
async def sandbox_v3_shop_buy(request: Request):
    call_obj = await call(request)
    return _shop_buy(call_obj, "V3") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/shopSell")
async def sandbox_v3_shop_sell(request: Request):
    call_obj = await call(request)
    return _shop_sell(call_obj, "V3") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/shopBuyRecruit")
async def sandbox_v3_shop_buy_recruit(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    root, run, topic = _topic(call_obj, "V3")
    char_id = body_id(call_obj.body, "charId", "id")
    if not char_id:
        return fail("charId required")
    unique_append(ensure_list(ensure_dict(run, "dailyRecruit"), "recruited"), char_id)
    return finish(call_obj, "sandboxPerm")


@sandbox_route("/sandboxPerm/sandboxV3/shopRefresh")
async def sandbox_v3_shop_refresh(request: Request):
    call_obj = await call(request)
    return _shop_refresh(call_obj, "V3") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/switchMode")
async def sandbox_v3_switch_mode(request: Request):
    call_obj = await call(request)
    return _switch_mode(call_obj, "V3") if call_obj else auth_error()


@sandbox_route("/sandboxPerm/sandboxV3/unlockTech")
async def sandbox_v3_unlock_tech(request: Request):
    call_obj = await call(request)
    return _unlock_tech(call_obj, "V3") if call_obj else auth_error()
