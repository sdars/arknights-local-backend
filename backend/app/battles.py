import hashlib
import json
import threading
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import game_data, grants, missions
from .db import connect, now_ts
from .services import account_by_uid, player_data, save_player


LOG_TOKEN_KEY = "pM6Umv*^hVQuB6t&"
_lock = threading.Lock()

PLAYER_EXP = [
    500, 800, 1240, 1320, 1400, 1480, 1560, 1640, 1720, 1800, 1880, 1960,
    2040, 2120, 2200, 2280, 2360, 2440, 2520, 2600, 2680, 2760, 2840, 2920,
    3000, 3080, 3160, 3240, 3350, 3460, 3570, 3680, 3790, 3900, 4200, 4500,
    4800, 5100, 5400, 5700, 6000, 6300, 6600, 6900, 7200, 7500, 7800, 8100,
    8400, 8700, 9000, 9500, 10000, 10500, 11000, 11500, 12000, 12500, 13000,
    13500, 14000, 14500, 15000, 15500, 16000, 17000, 18000, 19000, 20000,
    21000, 22000, 23000, 24000, 25000, 26000, 27000, 28000, 29000, 30000,
    31000, 32000, 33000, 34000, 35000, 36000, 37000, 38000, 39000, 40000,
    41000, 42000, 43000, 44000, 45000, 46000, 47000, 48000, 49000, 50000,
    51000, 52000, 54000, 56000, 58000, 60000, 62000, 64000, 66000, 68000,
    70000, 73000, 76000, 79000, 82000, 85000, 88000, 91000, 94000, 97000,
    100000,
]

PLAYER_AP = [
    82, 84, 86, 88, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102,
    103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116,
    117, 118, 119, 120, 120, 120, 120, 120, 121, 121, 121, 121, 121, 122,
    122, 122, 122, 122, 123, 123, 123, 123, 123, 124, 124, 124, 124, 124,
    125, 125, 125, 125, 125, 126, 126, 126, 126, 126, 127, 127, 127, 127,
    127, 128, 128, 128, 128, 128, 129, 129, 129, 129, 129, 130, 130, 130,
    130, 130, 130, 130, 130, 130, 130, 130, 130, 130, 130, 130, 130, 130,
    130, 131, 131, 131, 131, 132, 132, 132, 132, 133, 133, 133, 133, 134,
    134, 134, 134, 135, 135, 135, 135,
]


def _dict(parent, key):
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def decrypt_battle_data(encoded, login_time):
    if not isinstance(encoded, str) or len(encoded) < 34:
        return None
    try:
        body = bytes.fromhex(encoded[:-32])
        iv = bytes.fromhex(encoded[-32:])
        key = hashlib.md5((LOG_TOKEN_KEY + str(login_time)).encode("utf-8")).digest()
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        raw = decryptor.update(body) + decryptor.finalize()
        if raw:
            padding = raw[-1]
            if 1 <= padding <= 16 and raw.endswith(bytes([padding]) * padding):
                raw = raw[:-padding]
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def record_battle_start(account_id, battle_id, stage_id):
    with connect() as conn:
        conn.execute(
            "INSERT INTO battle_sessions(battle_id,account_id,stage_id,started_at,finished_at) VALUES(?,?,?,?,NULL)",
            (battle_id, account_id, stage_id, now_ts()),
        )


def _battle_session(account_id, battle_id=None):
    with connect() as conn:
        if battle_id:
            row = conn.execute(
                "SELECT * FROM battle_sessions WHERE battle_id=? AND account_id=?",
                (battle_id, account_id),
            ).fetchone()
            if row:
                return dict(row)
        row = conn.execute(
            "SELECT * FROM battle_sessions WHERE account_id=? ORDER BY started_at DESC LIMIT 1",
            (account_id,),
        ).fetchone()
    return dict(row) if row else None


def _close_battle(battle_id):
    if not battle_id:
        return
    with connect() as conn:
        conn.execute("UPDATE battle_sessions SET finished_at=? WHERE battle_id=?", (now_ts(), battle_id))


def _grant_player_exp(user, amount):
    if amount <= 0:
        return
    status = _dict(user, "status")
    level = int(status.get("level", 1) or 1)
    if level >= 120:
        return
    status["exp"] = int(status.get("exp", 0) or 0) + amount
    while level < 120:
        threshold = PLAYER_EXP[level - 1]
        if int(status.get("exp", 0) or 0) < threshold:
            break
        status["exp"] = int(status.get("exp", 0) or 0) - threshold
        level += 1
        if level - 1 < len(PLAYER_AP):
            status["maxAp"] = PLAYER_AP[level - 1]
            status["ap"] = int(status.get("ap", 0) or 0) + PLAYER_AP[level - 1]
        status["lastApAddTime"] = now_ts()
    status["level"] = level
    if level >= 120:
        status["exp"] = 0


def _stage_entry(stages, stage_id):
    node = stages.get(stage_id)
    if not isinstance(node, dict):
        node = {
            "hasBattleReplay": 0,
            "noCostCnt": 0,
            "practiceTimes": 0,
            "completeTimes": 0,
            "state": 0,
            "stageId": stage_id,
            "startTimes": 0,
        }
        stages[stage_id] = node
    return node


def _default_response():
    return {
        "result": 0,
        "apFailReturn": 0,
        "goldScale": 1,
        "expScale": 1,
        "rewards": [],
        "firstRewards": [],
        "unusualRewards": [],
        "overrideRewards": [],
        "additionalRewards": [],
        "diamondMaterialRewards": [],
        "furnitureRewards": [],
        "unlockStages": [],
        "pryResult": [],
        "alert": [],
        "suggestFriend": True,
        "pushMessage": [],
        "playerDataDelta": grants.empty_delta(),
    }


def finish_battle(account, body):
    with _lock:
        fresh = account_by_uid(account["uid"])
        if not fresh:
            return {"result": 2, "error": "account not found"}
        user = player_data(fresh)
        push_flags = user.get("pushFlags") or {}
        decrypted = decrypt_battle_data(body.get("data"), push_flags.get("status", ""))
        decode = decrypted or {}
        battle_id = str(
            decode.get("battleId")
            or body.get("battleId")
            or body.get("stageId")
            or ""
        )
        session = _battle_session(fresh["id"], battle_id or None)
        stage_id = str(
            decode.get("battleId")
            or body.get("stageId")
            or (session or {}).get("stage_id")
            or body.get("battleId")
            or ""
        )
        if not stage_id and session:
            stage_id = str(session.get("stage_id") or "")
        stage_cfg = game_data.stage(stage_id) if stage_id else None
        if not stage_cfg:
            _close_battle((session or {}).get("battle_id"))
            response = _default_response()
            response["result"] = 1
            response["error"] = f"unknown stage: {stage_id or 'missing'}"
            return response

        body_result = body.get("result")
        complete_state = int(
            decode.get("completeState")
            or body.get("completeState")
            or (3 if body_result in (None, 0, "0") else 1)
        )
        complete_state = max(1, min(complete_state, 3))

        dungeon = _dict(user, "dungeon")
        stages = _dict(dungeon, "stages")
        stage_node = _stage_entry(stages, stage_id)
        first_clear = int(stage_node.get("completeTimes", 0) or 0) == 0
        stage_node["stageId"] = stage_id
        stage_node["startTimes"] = max(1, int(stage_node.get("startTimes", 0) or 0))
        passed = complete_state >= 2
        if passed:
            stage_node["completeTimes"] = int(stage_node.get("completeTimes", 0) or 0) + 1
            stage_node["state"] = max(int(stage_node.get("state", 0) or 0), complete_state)
        else:
            stage_node["state"] = max(1, int(stage_node.get("state", 0) or 0))

        response = _default_response()
        delta = response["playerDataDelta"]
        delta["modified"]["dungeon"] = user.get("dungeon", {})

        if passed:
            scale = 1.2 if complete_state == 3 else 1.0
            gold = int(int(stage_cfg.get("goldGain", 0) or 0) * scale)
            exp = int(int(stage_cfg.get("expGain", 0) or 0) * scale)
            response["goldScale"] = scale
            response["expScale"] = scale

            normal, additional = game_data.normal_stage_rewards(stage_id)
            reward_items = ([{"id": "4001", "type": "GOLD", "count": gold}] if gold else []) + normal
            first_items = game_data.first_clear_rewards(stage_id) if first_clear else []
            _grant_player_exp(user, exp)

            _, item_delta = grants.apply_items(
                user, reward_items + first_items + additional, source="battle"
            )
            grants.merge_delta(delta, item_delta)
            delta["modified"]["status"] = user.get("status", {})
            response["rewards"] = reward_items
            response["firstRewards"] = first_items
            response["additionalRewards"] = additional

            unlocked = []
            for target in game_data.next_stages(stage_id):
                if target in stages:
                    continue
                stages[target] = {
                    "hasBattleReplay": 0,
                    "practiceTimes": 0,
                    "state": 0,
                    "completeTimes": 0,
                    "stageId": target,
                    "startTimes": 0,
                }
                unlocked.append(target)
            if unlocked:
                response["unlockStages"] = unlocked
                delta["modified"]["dungeon"] = user.get("dungeon", {})

            completed_missions = missions.record_stage_clear(user, stage_id, complete_state)
            if completed_missions:
                delta["modified"]["mission"] = user.get("mission", {})

        save_player(fresh, user)
        _close_battle((session or {}).get("battle_id") or battle_id or None)
        return response
