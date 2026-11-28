"""Integrated strategy (Roguelike V2) endpoints.

The client uses ``user.rlv2.current`` for the active run and
``user.rlv2.outer`` for the persistent progression record.  This module keeps
those two structures authoritative; every mutating action writes the run
state before returning the normal delta envelope.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Request

from .. import game_data
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
    unique_append,
)


router = APIRouter()


def rlv2_route(path: str):
    def decorator(fn: Callable):
        router.add_api_route(path, fn, methods=["POST"], name=fn.__name__)
        return fn

    return decorator


def _root(call_obj):
    return ensure_dict(call_obj.user, "rlv2")


def _current(call_obj):
    return ensure_dict(_root(call_obj), "current")


def _outer(call_obj):
    return ensure_dict(_root(call_obj), "outer")


def _theme(call_obj) -> str:
    game = ensure_dict(_current(call_obj), "game")
    theme = body_id(call_obj.body, "theme", "topicId") or str(game.get("theme") or "rogue_5")
    return theme


def _outer_topic(call_obj) -> dict:
    outer = _outer(call_obj)
    theme = _theme(call_obj)
    node = outer.get(theme)
    if not isinstance(node, dict):
        node = {}
        outer[theme] = node
    node.setdefault("bank", {"reward": {}, "current": 0, "record": 0, "show": False, "totalPut": 0})
    node.setdefault("record", {"bandCnt": {}, "stageCnt": {}, "last": 0, "modeCnt": {}, "endingCnt": {}})
    node.setdefault("battlePass", {"level": 1, "point": 0, "bought": [], "reward": {}})
    node.setdefault("shop", {"goods": [], "refresh": 0})
    return node


def _player(call_obj):
    return ensure_dict(_current(call_obj), "player")


def _inventory(call_obj):
    return ensure_dict(_current(call_obj), "inventory")


def _add_item(call_obj, bucket: str, item_id: str, count: int = 1) -> None:
    inventory = _inventory(call_obj)
    node = ensure_dict(inventory, bucket)
    node[item_id] = as_int(node.get(item_id), 0) + max(1, count)


def _pending(call_obj, pending_type: str, **payload) -> None:
    player = _player(call_obj)
    pending = ensure_list(player, "pending")
    item = {"type": pending_type, "index": [len(pending) + 1, len(pending) + 1]}
    item.update(payload)
    pending.append(item)


def _create_game(call_obj):
    current = _current(call_obj)
    theme = body_id(call_obj.body, "theme", "topic", default="rogue_5")
    mode = body_id(call_obj.body, "mode", default="EASY")
    grade = max(0, as_int(call_obj.body.get("modeGrade"), 0))
    current.clear()
    current.update(
        {
            "game": {
                "mode": mode,
                "outer": {"legacy": [], "support": False},
                "start": now_ts(),
                "modeGrade": grade,
                "theme": theme,
                "equivalentGrade": grade,
                "seed": body_id(call_obj.body, "seed", default=str(now_ts())),
                "predefined": body_id(call_obj.body, "predefinedId", default=""),
            },
            "module": {},
            "record": {},
            "troop": {"hasExpeditionReturn": False, "expedition": [], "chars": {}, "expeditionDetails": {}},
            "inventory": {"exploreTool": {}, "relic": {}, "recruit": {}, "consumable": {}},
            "map": {"zones": {}, "node": {}},
            "buff": {"tmpHP": 0, "squadBuff": []},
            "player": {
                "cursor": {"zone": 0},
                "trace": [],
                "toEnding": "",
                "pending": [
                    {"type": "RELIC", "index": [1, 4]},
                    {"type": "EXPLORE_TOOL", "index": [2, 4]},
                    {"type": "RECRUIT_SET", "index": [3, 4]},
                    {"type": "RECRUIT", "index": [4, 4]},
                ],
                "chgEnding": False,
                "property": {
                    "gold": max(0, as_int(call_obj.body.get("gold"), 8)),
                    "maxLevel": 10,
                    "shield": 0,
                    "level": 1,
                    "hp": {"current": 8, "max": 8},
                    "exp": 0,
                    "conPerfectBattle": 0,
                    "capacity": 6,
                    "population": {"cost": 0, "max": 6},
                },
                "state": "INIT",
                "status": {"bankPut": 0},
            },
        }
    )
    _outer_topic(call_obj)["record"]["last"] = now_ts()
    call_obj.touch("rlv2")
    result = finish(call_obj, "rlv2")
    result["game"] = current["game"]
    result["pending"] = current["player"]["pending"]
    return result


def _battle_start(call_obj, prefix: str = "rlv2"):
    current = _current(call_obj)
    player = ensure_dict(current, "player")
    player["state"] = "BATTLE"
    player["battleId"] = battle_id(prefix, call_obj.account)
    player["battleStage"] = body_id(call_obj.body, "stageId", "nodeId", "to")
    player["battleSquad"] = first_value(call_obj.body, "squad", "slots", default=[])
    player["battleStartTs"] = now_ts()
    call_obj.touch("rlv2")
    result = finish(call_obj, "rlv2")
    result["battleId"] = player["battleId"]
    return result


def _battle_finish(call_obj):
    current = _current(call_obj)
    player = ensure_dict(current, "player")
    player["state"] = "MAP"
    player["battleFinishedTs"] = now_ts()
    player["lastBattleResult"] = "WIN" if as_int(call_obj.body.get("completeState"), 3) >= 2 else "LOSE"
    if player["lastBattleResult"] == "WIN":
        prop = ensure_dict(player, "property")
        prop["exp"] = as_int(prop.get("exp"), 0) + 10
        if prop["exp"] >= as_int(prop.get("maxLevel"), 10) * 10:
            prop["level"] = as_int(prop.get("level"), 1) + 1
            prop["exp"] = 0
    _add_item(call_obj, "consumable", body_id(call_obj.body, "dropId", default="rlv2_battle_drop"), 1)
    call_obj.touch("rlv2")
    result = finish(call_obj, "rlv2")
    result["items"] = []
    return result


def _zone(call_obj, zone_id: str | None = None):
    map_node = ensure_dict(_current(call_obj), "map")
    zones = ensure_dict(map_node, "zones")
    key = str(zone_id or body_id(call_obj.body, "zoneId", "to", "nodeId", default="0"))
    return ensure_dict(zones, key)


def _move(call_obj, battle: bool = False):
    player = _player(call_obj)
    cursor = ensure_dict(player, "cursor")
    target = first_value(call_obj.body, "to", "zone", "nodeId", default=cursor.get("zone", 0))
    if isinstance(target, dict):
        target = target.get("zone") or target.get("id") or cursor.get("zone", 0)
    cursor["zone"] = as_int(target, as_int(cursor.get("zone"), 0))
    trace = ensure_list(player, "trace")
    trace.append({"zone": cursor["zone"], "ts": now_ts()})
    _zone(call_obj, str(cursor["zone"]))["visited"] = True
    player["state"] = "BATTLE" if battle else "MAP"
    if battle:
        player["battleId"] = battle_id("rlv2-move", call_obj.account)
    return finish(call_obj, "rlv2") if not battle else {**finish(call_obj, "rlv2"), "battleId": player["battleId"]}


def _recruit(call_obj, assist: bool = False):
    ticket_index = as_int(call_obj.body.get("ticketIndex"), 0)
    char_id = body_id(call_obj.body, "charId", "assistCharId", "id")
    if not char_id:
        return fail("charId required")
    recruits = ensure_dict(_inventory(call_obj), "recruit")
    recruits[str(ticket_index)] = {"charId": char_id, "assist": assist, "ts": now_ts()}
    troop = ensure_dict(_current(call_obj), "troop")
    chars = ensure_dict(troop, "chars")
    chars[char_id] = {"charId": char_id, "level": 1, "evolvePhase": 0, "potentialRank": 0}
    call_obj.touch("rlv2")
    result = finish(call_obj, "rlv2")
    result["chars"] = list(chars.values())
    return result


def _finish_event(call_obj):
    player = _player(call_obj)
    pending = ensure_list(player, "pending")
    pending_type = body_id(call_obj.body, "type", "eventType")
    if pending_type:
        pending[:] = [row for row in pending if not (isinstance(row, dict) and row.get("type") == pending_type)]
    else:
        pending.clear()
    player["state"] = "MAP"
    return finish(call_obj, "rlv2")


def _shop(call_obj, action: str):
    topic = _outer_topic(call_obj)
    shop = ensure_dict(topic, "shop")
    if action == "refresh":
        shop["refresh"] = as_int(shop.get("refresh"), 0) + 1
        shop["refreshTs"] = now_ts()
    elif action in {"buy", "recycle"}:
        goods = ensure_dict(shop, "goods")
        good_id = body_id(call_obj.body, "goodId", "id", "itemId")
        if not good_id:
            return fail("goodId required")
        good = ensure_dict(goods, good_id)
        if action == "buy":
            cost = max(0, as_int(call_obj.body.get("cost"), as_int(good.get("cost"), 0)))
            gold = as_int(ensure_dict(_player(call_obj), "property").get("gold"), 0)
            if cost > gold:
                return fail("not enough gold")
            ensure_dict(_player(call_obj), "property")["gold"] = gold - cost
            good["bought"] = as_int(good.get("bought"), 0) + 1
            _add_item(call_obj, "consumable", good_id, 1)
        else:
            _add_item(call_obj, "consumable", good_id, -1)
    return finish(call_obj, "rlv2")


def _generic_action(call_obj, action: str):
    """Apply a concrete operation for actions that only touch run-local state."""
    current = _current(call_obj)
    player = _player(call_obj)
    module = ensure_dict(current, "module")
    if action == "activeRecruitTicket":
        tickets = ensure_list(player, "tickets")
        ticket_id = body_id(call_obj.body, "id", "ticketId", default=f"ticket_{len(tickets) + 1}")
        unique_append(tickets, ticket_id)
    elif action == "alchemy" or action == "alchemyReward":
        fragment = as_int(first_value(call_obj.body, "fragmentIndex", "index", default=0), 0)
        alchemy = ensure_dict(module, "alchemy")
        if action == "alchemy":
            alchemy[str(fragment)] = {"state": "BREWING", "startTs": now_ts()}
        else:
            node = ensure_dict(alchemy, str(fragment))
            node["state"] = "DONE"
            node["claimedTs"] = now_ts()
            call_obj.extra["items"] = apply_items(call_obj, [{"id": "4001", "type": "GOLD", "count": 200}], "rlv2-alchemy")
    elif action in {"bankPut", "bankWithdraw"}:
        topic = _outer_topic(call_obj)
        bank = ensure_dict(topic, "bank")
        amount = max(0, as_int(call_obj.body.get("amount"), 0))
        prop = ensure_dict(player, "property")
        if action == "bankPut":
            amount = min(amount, as_int(prop.get("gold"), 0))
            prop["gold"] = as_int(prop.get("gold"), 0) - amount
            bank["current"] = as_int(bank.get("current"), 0) + amount
            bank["totalPut"] = as_int(bank.get("totalPut"), 0) + amount
        else:
            amount = min(amount, as_int(bank.get("current"), 0))
            bank["current"] = as_int(bank.get("current"), 0) - amount
            prop["gold"] = as_int(prop.get("gold"), 0) + amount
        player["status"]["bankPut"] = as_int(player.get("status", {}).get("bankPut"), 0) + amount
    elif action == "battlePass/buyReward":
        topic = _outer_topic(call_obj)
        bp = ensure_dict(topic, "battlePass")
        reward_id = body_id(call_obj.body, "reward", "rewardId", "id")
        unique_append(ensure_list(bp, "bought"), reward_id)
    elif action == "battlePass/getReward":
        topic = _outer_topic(call_obj)
        bp = ensure_dict(topic, "battlePass")
        rewards = call_obj.body.get("rewards") or call_obj.body.get("reward")
        if not isinstance(rewards, list):
            rewards = [rewards] if rewards else []
        claimed = ensure_dict(bp, "reward")
        for reward_id in rewards:
            if reward_id and not claimed.get(str(reward_id)):
                claimed[str(reward_id)] = 1
        call_obj.extra["items"] = apply_items(call_obj, [{"id": "4001", "type": "GOLD", "count": 500}], "rlv2-battlepass")
    elif action in {"chooseBattleReward", "finishBattleReward", "confirmZoneReward"}:
        item_id = body_id(call_obj.body, "itemId", "relicId", "id", default="rlv2_relic")
        _add_item(call_obj, "relic" if "relic" in item_id or action != "finishBattleReward" else "consumable", item_id, 1)
        call_obj.extra["items"] = []
    elif action in {"chooseInitialExploreTool", "chooseInitialRelic", "chooseInitialRecruitSet"}:
        item_id = body_id(call_obj.body, "itemId", "select", "id", default=action)
        bucket = "exploreTool" if action == "chooseInitialExploreTool" else "relic" if action == "chooseInitialRelic" else "recruit"
        _add_item(call_obj, bucket, item_id, 1)
        pending_type = {
            "chooseInitialExploreTool": "EXPLORE_TOOL",
            "chooseInitialRelic": "RELIC",
            "chooseInitialRecruitSet": "RECRUIT_SET",
        }[action]
        player["pending"] = [
            row for row in ensure_list(player, "pending")
            if isinstance(row, dict) and row.get("type") != pending_type
        ]
        call_obj.extra["items"] = [{"id": item_id, "type": "MATERIAL", "count": 1}]
    elif action == "closeRecruitTicket":
        ensure_dict(_inventory(call_obj), "recruit").pop(body_id(call_obj.body, "id", "ticketId"), None)
    elif action == "confirmPredict":
        player["predictConfirmed"] = True
    elif action == "confirmTraderReturn":
        player["traderReturned"] = True
    elif action == "diceChoice":
        module["diceChoice"] = body_id(call_obj.body, "choice", "id", default="0")
    elif action == "sacrificeChoice":
        module["sacrificeChoice"] = {
            "choice": body_id(call_obj.body, "choice", "id"),
            "leave": bool(call_obj.body.get("leave")),
        }
    elif action == "getTicketAssistList":
        chars = ensure_dict(ensure_dict(current, "troop"), "chars")
        if not chars:
            source = ensure_dict(ensure_dict(call_obj.user, "troop"), "chars")
            for char in source.values():
                if isinstance(char, dict):
                    chars[str(char.get("instId") or len(chars))] = {
                        "charId": char.get("charId"),
                        "level": char.get("level"),
                        "evolvePhase": char.get("evolvePhase"),
                        "potentialRank": char.get("potentialRank"),
                    }
        call_obj.extra["assistList"] = list(chars.values())
    elif action == "expeditionChoice":
        module["expeditionChoice"] = {"choice": body_id(call_obj.body, "choice", "id"), "leave": bool(call_obj.body.get("leave"))}
    elif action == "readEndingChange":
        player["chgEnding"] = False
        player["endingReadTs"] = now_ts()
    elif action == "loseFragment":
        props = ensure_dict(player, "property")
        props["hp"] = max(0, as_int(as_dict(props.get("hp")).get("current"), 0) - 1)
    elif action == "useInspiration":
        module["inspirationUsed"] = as_int(module.get("inspirationUsed"), 0) + 1
    elif action == "useTotem":
        module["totemUsed"] = body_id(call_obj.body, "totemId", "id", default="totem")
    elif action == "setPinned":
        module["pinned"] = body_id(call_obj.body, "id", "nodeId", default="")
    elif action == "setSeed":
        current["game"]["seed"] = body_id(call_obj.body, "seed", default=str(now_ts()))
    elif action == "setTroopCarry":
        current["troop"]["troopCarry"] = call_obj.body.get("troopCarry") or call_obj.body.get("chars") or []
    elif action == "normal/refreshMission":
        topic = _outer_topic(call_obj)
        mission = ensure_dict(topic, "mission")
        mission["refresh"] = now_ts()
        mission["list"] = []
    elif action == "normal/unlockBuff":
        topic = _outer_topic(call_obj)
        buff = ensure_dict(topic, "buff")
        buff_id = body_id(call_obj.body, "buff", "id")
        if buff_id:
            ensure_dict(buff, "unlocked")[buff_id] = 1
    elif action == "specialZone/leave":
        player["cursor"] = {"zone": 0}
    elif action in {"game/confirmExpeditonReturn", "confirmExpeditionReturn"}:
        current["troop"]["hasExpeditionReturn"] = False
    elif action == "giveUpGame":
        player["state"] = "NONE"
        current["game"]["endTs"] = now_ts()
    elif action == "gameSettle":
        _outer_topic(call_obj)["record"]["last"] = now_ts()
        player["state"] = "NONE"
        current["record"]["settled"] = True
    elif action == "readStepZero":
        player["stepZeroRead"] = True
    elif action == "emptyStep":
        _move(call_obj, battle=False)
    elif action == "moveTo":
        _move(call_obj, battle=False)
    elif action == "moveAndBattleStart":
        _move(call_obj, battle=True)
    elif action == "gridZone/emptyStep":
        _move(call_obj, battle=False)
    elif action == "gridZone/moveTo":
        _move(call_obj, battle=False)
    elif action == "gridZone/moveAndBattleStart":
        _move(call_obj, battle=True)
    elif action == "rerollNode":
        module["rerollNode"] = as_int(module.get("rerollNode"), 0) + 1
    elif action == "upgradeNode":
        module["upgradeNode"] = body_id(call_obj.body, "nodeType", "nodeId", default="")
    elif action == "scrap/changeVehicle":
        module["scrapVehicle"] = body_id(call_obj.body, "scrapInstId", "vehicleId", "id")
    elif action == "scrap/identify":
        module["identified"] = as_int(module.get("identified"), 0) + 1
        call_obj.extra["scrap"] = []
        call_obj.extra["legacy"] = []
    elif action == "scrap/loseScrap":
        module["lostScrap"] = body_id(call_obj.body, "instId", "id")
    elif action.startswith("copper/"):
        copper = ensure_dict(module, "copper")
        if action == "copper/change":
            copper["index"] = as_int(call_obj.body.get("index"), 0)
        elif action == "copper/gild":
            copper["gild"] = body_id(call_obj.body, "choice", "id")
        elif action == "copper/redraw":
            copper["redraw"] = call_obj.body.get("freezeIndexes") if isinstance(call_obj.body.get("freezeIndexes"), list) else []
        else:
            copper["confirmed"] = True
    elif action == "stashRecruitTicket":
        ticket = body_id(call_obj.body, "id", "ticketId", default="ticket")
        unique_append(ensure_list(player, "stashedTickets"), ticket)
    elif action == "useStashedTicket":
        ticket = body_id(call_obj.body, "id", "ticketId", default="ticket")
        stashed = ensure_list(player, "stashedTickets")
        if ticket in stashed:
            stashed.remove(ticket)
        unique_append(ensure_list(player, "tickets"), ticket)
    elif action == "shopBattleStart":
        player["state"] = "BATTLE"
        player["battleId"] = battle_id("rlv2-shop", call_obj.account)
    else:
        return None
    call_obj.touch("rlv2")
    result = finish(call_obj, "rlv2")
    if action == "shopBattleStart":
        result["battleId"] = player["battleId"]
    return result


def _run(call_obj, action: str):
    if action == "createGame":
        return _create_game(call_obj)
    if action == "giveUpGame":
        return _generic_action(call_obj, action)
    if action in {"battleStart", "moveAndBattleStart", "gridZone/moveAndBattleStart", "shopBattleStart"}:
        if action == "shopBattleStart":
            return _generic_action(call_obj, action)
        return _battle_start(call_obj, "rlv2")
    if action == "battleFinish":
        return _battle_finish(call_obj)
    if action == "recruitChar":
        return _recruit(call_obj, False)
    if action == "recruitAssistChar":
        return _recruit(call_obj, True)
    if action == "finishEvent":
        return _finish_event(call_obj)
    if action == "refreshShop":
        return _shop(call_obj, "refresh")
    return _generic_action(call_obj, action)


# The paths below are the complete protocol surface for /rlv2.  Keeping the
# route and operation names explicit makes an unimplemented action fail in its
# own function rather than being accepted by a wildcard handler.
RLV2_ACTIONS = {
    "/rlv2/activeRecruitTicket": "activeRecruitTicket",
    "/rlv2/alchemy": "alchemy",
    "/rlv2/alchemyReward": "alchemyReward",
    "/rlv2/bankPut": "bankPut",
    "/rlv2/bankWithdraw": "bankWithdraw",
    "/rlv2/battleFinish": "battleFinish",
    "/rlv2/battlePass/buyReward": "battlePass/buyReward",
    "/rlv2/battlePass/getReward": "battlePass/getReward",
    "/rlv2/chooseBattleReward": "chooseBattleReward",
    "/rlv2/chooseInitialExploreTool": "chooseInitialExploreTool",
    "/rlv2/chooseInitialRecruitSet": "chooseInitialRecruitSet",
    "/rlv2/chooseInitialRelic": "chooseInitialRelic",
    "/rlv2/closeRecruitTicket": "closeRecruitTicket",
    "/rlv2/confirmPredict": "confirmPredict",
    "/rlv2/confirmTraderReturn": "confirmTraderReturn",
    "/rlv2/confirmZoneReward": "confirmZoneReward",
    "/rlv2/copper/change": "copper/change",
    "/rlv2/copper/confirmDraw": "copper/confirmDraw",
    "/rlv2/copper/gild": "copper/gild",
    "/rlv2/copper/redraw": "copper/redraw",
    "/rlv2/createGame": "createGame",
    "/rlv2/diceChoice": "diceChoice",
    "/rlv2/expeditionChoice": "expeditionChoice",
    "/rlv2/finishBattleReward": "finishBattleReward",
    "/rlv2/finishEvent": "finishEvent",
    "/rlv2/game/confirmExpeditonReturn": "game/confirmExpeditonReturn",
    "/rlv2/gameSettle": "gameSettle",
    "/rlv2/getTicketAssistList": "getTicketAssistList",
    "/rlv2/giveUpGame": "giveUpGame",
    "/rlv2/gridZone/emptyStep": "gridZone/emptyStep",
    "/rlv2/gridZone/moveAndBattleStart": "gridZone/moveAndBattleStart",
    "/rlv2/gridZone/moveTo": "gridZone/moveTo",
    "/rlv2/gridZone/readStepZero": "readStepZero",
    "/rlv2/loseFragment": "loseFragment",
    "/rlv2/moveAndBattleStart": "moveAndBattleStart",
    "/rlv2/normal/refreshMission": "normal/refreshMission",
    "/rlv2/normal/unlockBuff": "normal/unlockBuff",
    "/rlv2/readEndingChange": "readEndingChange",
    "/rlv2/recruitAssistChar": "recruitAssistChar",
    "/rlv2/recruitChar": "recruitChar",
    "/rlv2/refreshShop": "refreshShop",
    "/rlv2/rerollNode": "rerollNode",
    "/rlv2/sacrificeChoice": "sacrificeChoice",
    "/rlv2/scrap/changeVehicle": "scrap/changeVehicle",
    "/rlv2/scrap/identify": "scrap/identify",
    "/rlv2/scrap/loseScrap": "scrap/loseScrap",
    "/rlv2/setPinned": "setPinned",
    "/rlv2/setSeed": "setSeed",
    "/rlv2/setTroopCarry": "setTroopCarry",
    "/rlv2/shopBattleStart": "shopBattleStart",
    "/rlv2/specialZone/leave": "specialZone/leave",
    "/rlv2/stashRecruitTicket": "stashRecruitTicket",
    "/rlv2/upgradeNode": "upgradeNode",
    "/rlv2/useInspiration": "useInspiration",
    "/rlv2/useStashedTicket": "useStashedTicket",
    "/rlv2/useTotem": "useTotem",
}


async def _dispatch(request: Request, action: str):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    result = _run(call_obj, action)
    if result is None:
        return fail(f"unsupported rlv2 action: {action}")
    return result


for _path, _action in RLV2_ACTIONS.items():
    def _make_endpoint(action: str):
        async def endpoint(request: Request):
            return await _dispatch(request, action)

        endpoint.__name__ = "rlv2_" + action.replace("/", "_").replace("-", "_")
        return endpoint

    router.add_api_route(_path, _make_endpoint(_action), methods=["POST"], name="rlv2_" + _action.replace("/", "_"))
