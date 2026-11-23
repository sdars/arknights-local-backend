import hashlib
import json
import re
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
            "INSERT OR REPLACE INTO battle_sessions(battle_id,account_id,stage_id,started_at,finished_at) VALUES(?,?,?,?,NULL)",
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
        battle_id = str(decode.get("battleId") or body.get("battleId") or body.get("stageId") or "")
        session = _battle_session(fresh["id"], battle_id or None)
        candidates = [
            decode.get("stageId"),
            decode.get("battleId"),
            body.get("stageId"),
            (session or {}).get("stage_id"),
            body.get("battleId"),
        ]
        stage_id = ""
        for candidate in candidates:
            text = str(candidate or "")
            if text and game_data.stage(text):
                stage_id = text
                break
        if not stage_id:
            stage_id = str((session or {}).get("stage_id") or decode.get("battleId") or body.get("battleId") or "")
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


def _main_code(stage_id):
    entry = game_data.stage(stage_id) or {}
    code = str(entry.get("code") or "")
    match = re.match(r"^(\d+)-(\d+)$", code)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def main_stage_sequence():
    stages = (game_data.stage_table() or {}).get("stages") or {}
    rows = []
    for stage_id, entry in stages.items():
        if not isinstance(entry, dict) or entry.get("stageType") != "MAIN":
            continue
        code = _main_code(stage_id)
        if code:
            rows.append((code, str(stage_id)))
    rows.sort()
    return [stage_id for _, stage_id in rows]


def normalize_main_stage(target):
    text = str(target or "").strip()
    if not text:
        return ""
    if text.startswith("main_"):
        return text if game_data.stage(text) else ""
    match = re.match(r"^(\d+)-(\d+)$", text)
    if not match:
        return ""
    chapter = int(match.group(1))
    index = int(match.group(2))
    candidate = f"main_{chapter:02d}-{index:02d}"
    return candidate if game_data.stage(candidate) else ""


def _stage_node(stages, stage_id):
    node = stages.get(stage_id)
    if not isinstance(node, dict):
        node = {
            "hasBattleReplay": 0,
            "practiceTimes": 0,
            "state": 0,
            "completeTimes": 0,
            "stageId": stage_id,
            "startTimes": 0,
        }
        stages[stage_id] = node
    return node


def _stage_satisfies(stages, stage_id, minimum="PASS"):
    node = stages.get(str(stage_id or ""))
    if not isinstance(node, dict):
        return False
    state = int(node.get("state", 0) or 0)
    complete_times = int(node.get("completeTimes", 0) or 0)
    minimum = str(minimum or "PASS").upper()
    if minimum == "UNLOCKED":
        return bool(node)
    if minimum == "COMPLETE":
        return state >= 3 or complete_times > 0
    return state >= 2 or complete_times > 0


def _completed_stage_ids(user):
    stages = ((user.get("dungeon") or {}).get("stages")) or {}
    return {
        str(stage_id)
        for stage_id, node in stages.items()
        if isinstance(node, dict)
        and (int(node.get("state", 0) or 0) >= 2 or int(node.get("completeTimes", 0) or 0) > 0)
    }


def _story_requirements_met(story, stages, flags):
    condition = story.get("condition") or {}
    raw_min = condition.get("minProgress")
    min_progress = -1 if raw_min is None else int(raw_min)
    if min_progress < 0:
        return False
    for entry in condition.get("requiredStages") or []:
        if not isinstance(entry, dict):
            continue
        if not _stage_satisfies(stages, entry.get("stageId"), entry.get("minState") or "PASS"):
            return False
    for flag in condition.get("requiredFlags") or []:
        if not flags.get(str(flag)):
            return False
    for flag in condition.get("excludedFlags") or []:
        if flags.get(str(flag)):
            return False
    return True


def _derived_tutorial_progress(user):
    status = user.get("status") or {}
    stages = ((user.get("dungeon") or {}).get("stages")) or {}
    flags = status.get("flags") if isinstance(status.get("flags"), dict) else {}
    progress = int(status.get("progress") or 0)
    stories = game_data.excel("story_table.json") or {}
    for story_id, story in stories.items():
        if not str(story_id or "").startswith("obt/guide/") or not isinstance(story, dict):
            continue
        condition = story.get("condition") or {}
        raw_min = condition.get("minProgress")
        min_progress = -1 if raw_min is None else int(raw_min)
        if min_progress < 0:
            continue
        if not _story_requirements_met(story, stages, flags):
            continue
        if not condition.get("requiredStages"):
            continue
        progress = max(progress, min_progress, int(story.get("setProgress") or 0))
    return progress


def reconcile_tutorial_state(user):
    """Make tutorial flags consistent with unlocked and cleared stages.

    A manually advanced mainline without matching tutorial flags makes the
    client launch an old AVG tutorial whose UI target is no longer present.
    That ends in AVGController.NullReferenceException and the generic
    "disconnected, please log in again" dialog.  Mark every guide whose
    progress/requirements are already satisfied so the client cannot start it.
    """
    status = user.setdefault("status", {})
    stages = ((user.get("dungeon") or {}).get("stages")) or {}
    derived = _derived_tutorial_progress(user)
    if derived <= 0 and not _completed_stage_ids(user):
        return False
    old_progress = int(status.get("progress") or 0)
    progress = max(old_progress, derived)
    changed = progress != old_progress
    flags = status.get("flags")
    if not isinstance(flags, dict):
        flags = {}
        status["flags"] = flags
        changed = True
    if not flags.get("init"):
        flags["init"] = 1
        changed = True
    stories = game_data.excel("story_table.json") or {}
    guides = [
        (str(story_id or ""), story)
        for story_id, story in stories.items()
        if str(story_id or "").startswith("obt/guide/") and isinstance(story, dict)
    ]
    # Marking a tutorial as done without applying its setProgress leaves the
    # client stuck between two steps of the same tutorial chain (the building
    # tutorial is the clearest example: 1a is skipped, but 1b still requires
    # the progress value that 1a would have written).  Iterate until stable so
    # every auto-skipped step behaves exactly like a completed step.
    for _ in range(len(guides) + 1):
        round_changed = False
        for story_id, story in guides:
            condition = story.get("condition") or {}
            raw_min = condition.get("minProgress")
            min_progress = -1 if raw_min is None else int(raw_min)
            if min_progress < 0 or min_progress > progress:
                continue
            if not _story_requirements_met(story, stages, flags):
                continue
            if not flags.get(story_id):
                flags[story_id] = 1
                changed = True
                round_changed = True
            next_progress = int(story.get("setProgress") or -1)
            if next_progress > progress:
                progress = next_progress
                changed = True
                round_changed = True
        if not round_changed:
            break
    if progress != old_progress:
        status["progress"] = progress
    return changed


def tutorial_audit(user):
    status = user.get("status") or {}
    stages = ((user.get("dungeon") or {}).get("stages")) or {}
    flags = status.get("flags") if isinstance(status.get("flags"), dict) else {}
    progress = int(status.get("progress") or 0)
    stories = game_data.excel("story_table.json") or {}
    missing = []
    satisfied = 0
    for story_id, story in stories.items():
        story_id = str(story_id or "")
        if not story_id.startswith("obt/guide/") or not isinstance(story, dict):
            continue
        condition = story.get("condition") or {}
        raw_min = condition.get("minProgress")
        min_progress = -1 if raw_min is None else int(raw_min)
        if min_progress < 0 or min_progress > progress:
            continue
        if not _story_requirements_met(story, stages, flags):
            continue
        satisfied += 1
        if not flags.get(story_id):
            missing.append(story_id)
    return {"progress": progress, "satisfied": satisfied, "missing": missing, "ok": not missing}


def sync_mainline_progress(user, target):
    stage_id = normalize_main_stage(target)
    if not stage_id:
        return {"result": 1, "error": f"未知主线关卡：{target}"}
    sequence = main_stage_sequence()
    if stage_id not in sequence:
        return {"result": 1, "error": f"该关卡不在主线序列中：{stage_id}"}
    end = sequence.index(stage_id) + 1
    wanted = sequence[:end]
    dungeon = user.setdefault("dungeon", {})
    stages = dungeon.setdefault("stages", {})
    marked = 0
    for current in wanted:
        node = _stage_node(stages, current)
        node["stageId"] = current
        node["state"] = 3
        node["completeTimes"] = max(1, int(node.get("completeTimes", 0) or 0))
        node["startTimes"] = max(1, int(node.get("startTimes", 0) or 0))
        marked += 1
    tables = (game_data.stage_table() or {}).get("stages") or {}
    training = []
    for candidate_id, entry in tables.items():
        if not isinstance(entry, dict) or entry.get("stageType") != "MAIN":
            continue
        if not str(entry.get("code") or "").startswith("TR-"):
            continue
        conditions = [c for c in entry.get("unlockCondition") or [] if isinstance(c, dict)]
        if not conditions:
            continue
        if all(
            _stage_satisfies(stages, c.get("stageId"), c.get("completeState") or "PASS")
            for c in conditions
        ):
            training.append(str(candidate_id))
    for current in sorted(training):
        node = _stage_node(stages, current)
        node["stageId"] = current
        node["state"] = 3
        node["completeTimes"] = max(1, int(node.get("completeTimes", 0) or 0))
        node["startTimes"] = max(1, int(node.get("startTimes", 0) or 0))
        marked += 1
    status = user.setdefault("status", {})
    status["mainStageProgress"] = stage_id
    reconcile_tutorial_state(user)
    next_stage = sequence[end] if end < len(sequence) else ""
    if next_stage:
        _stage_node(stages, next_stage)
    return {
        "result": 0,
        "stageId": stage_id,
        "nextStage": next_stage,
        "marked": marked,
        "playerDataDelta": {
            "deleted": {},
            "modified": {"dungeon": dungeon, "status": status},
        },
    }


def repair_account_tutorial_states():
    repaired = 0
    with connect() as conn:
        rows = conn.execute("SELECT id,user_json FROM accounts").fetchall()
    for row in rows:
        try:
            user = json.loads(row["user_json"])
        except Exception:
            continue
        status = user.get("status") or {}
        target = normalize_main_stage(status.get("mainStageProgress"))
        changed = False
        if target and target != "main_00-01":
            result = sync_mainline_progress(user, target)
            changed = result.get("result") == 0
        else:
            changed = reconcile_tutorial_state(user)
        if not changed:
            continue
        with connect() as conn:
            conn.execute(
                "UPDATE accounts SET user_json=? WHERE id=?",
                (json.dumps(user, ensure_ascii=False, separators=(",", ":")), row["id"]),
            )
        repaired += 1
    return repaired
