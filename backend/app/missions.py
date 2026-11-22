import re

from . import game_data, grants


def _dict(parent, key):
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def mission_root(user):
    root = _dict(user, "mission")
    _dict(root, "missions")
    rewards = _dict(root, "missionRewards")
    rewards.setdefault("dailyPoint", 0)
    rewards.setdefault("weeklyPoint", 0)
    _dict(rewards, "rewards")
    _dict(root, "missionGroups")
    return root


def mission_bucket(user, mission_type, create=False):
    missions = _dict(mission_root(user), "missions")
    bucket = missions.get(mission_type)
    if not isinstance(bucket, dict):
        bucket = {}
        if create:
            missions[mission_type] = bucket
    return bucket


def _next_mission_id(mission_id, mission_type):
    match = re.match(r"^(.*_)(\d+)$", str(mission_id or ""))
    if not match:
        return None
    candidate = f"{match.group(1)}{int(match.group(2)) + 1}"
    entry = game_data.mission(candidate)
    if not entry or entry.get("type") != mission_type:
        return None
    return candidate


def _activate_next(user, mission_id, mission_type):
    candidate = _next_mission_id(mission_id, mission_type)
    if not candidate:
        return
    bucket = mission_bucket(user, mission_type, create=True)
    if candidate in bucket:
        return
    bucket[candidate] = {
        "state": 1,
        "progress": [{"value": 0, "target": 1}],
    }


def _complete_progress(node):
    progress = node.get("progress")
    if not isinstance(progress, list) or not progress:
        node["progress"] = [{"value": 1, "target": 1}]
        return
    changed = False
    for entry in progress:
        if not isinstance(entry, dict):
            continue
        target = int(entry.get("target", 1) or 1)
        if int(entry.get("value", 0) or 0) < target:
            entry["value"] = target
            changed = True
    if changed:
        node["progress"] = progress


def _success(items, extra=None):
    body = {"result": 0, "items": items, "playerDataDelta": grants.empty_delta()}
    if extra:
        body.update(extra)
    return body


def claim_missions(user, mission_ids, source="mission"):
    root = mission_root(user)
    claimed = []
    skipped = []
    reward_items = []
    seen = set()
    for raw_id in mission_ids or []:
        mission_id = str(raw_id or "")
        if not mission_id or mission_id in seen:
            continue
        seen.add(mission_id)
        entry = game_data.mission(mission_id)
        if not entry:
            skipped.append({"missionId": mission_id, "reason": "unknown"})
            continue
        mission_type = str(entry.get("type") or "").upper()
        bucket = mission_bucket(user, mission_type, create=False)
        node = bucket.get(mission_id)
        if not isinstance(node, dict):
            skipped.append({"missionId": mission_id, "reason": "not-in-save"})
            continue
        if int(node.get("state", 0) or 0) == 3:
            skipped.append({"missionId": mission_id, "reason": "already-claimed"})
            continue

        reward_items.extend(entry.get("rewards") or [])
        node["state"] = 3
        _complete_progress(node)
        points = int(entry.get("periodicalPoint", 0) or 0)
        if points:
            key = "weeklyPoint" if mission_type == "WEEKLY" else "dailyPoint"
            root["missionRewards"][key] = int(root["missionRewards"].get(key, 0) or 0) + points
        group_id = entry.get("missionGroup")
        if group_id:
            root["missionGroups"].setdefault(str(group_id), 1)
        _activate_next(user, mission_id, mission_type)
        claimed.append(mission_id)

    granted_items, delta = grants.apply_items(user, reward_items, source=source)
    delta["modified"]["mission"] = user.get("mission", {})
    return _success(granted_items, {"playerDataDelta": delta, "claimed": claimed, "skipped": skipped})


def auto_confirm(user, mission_type):
    mission_type = str(mission_type or "MAIN").upper()
    bucket = mission_bucket(user, mission_type, create=False)
    ids = [
        mission_id
        for mission_id, node in bucket.items()
        if isinstance(node, dict) and int(node.get("state", 0) or 0) == 2
    ]
    return claim_missions(user, ids, source="mission-auto")


def confirm_group(user, group_id):
    group = game_data.mission_group(group_id)
    if not group:
        return {"result": 1, "error": f"unknown mission group: {group_id}"}
    root = mission_root(user)
    states = root["missionGroups"]
    if int(states.get(group_id, 0) or 0) == 2:
        return _success([], {"playerDataDelta": grants.empty_delta()})
    for mission_id in group.get("missionIds") or []:
        entry = game_data.mission(mission_id) or {}
        bucket = mission_bucket(user, str(entry.get("type") or "").upper(), create=False)
        node = bucket.get(mission_id)
        if not isinstance(node, dict) or int(node.get("state", 0) or 0) != 3:
            return {"result": 1, "error": "mission group is not complete"}
    items, delta = grants.apply_items(user, group.get("rewards") or [], source="mission-group")
    states[group_id] = 2
    delta["modified"]["mission"] = user.get("mission", {})
    return _success(items, {"playerDataDelta": delta})


def exchange_reward(user, reward_id):
    reward = game_data.periodical_reward(reward_id)
    if not reward:
        return {"result": 1, "error": f"unknown mission reward: {reward_id}"}
    mission_type = str(reward.get("type") or "DAILY").upper()
    root = mission_root(user)
    claims = root["missionRewards"]["rewards"]
    type_claims = claims.get(mission_type)
    if not isinstance(type_claims, dict):
        type_claims = {}
        claims[mission_type] = type_claims
    if int(type_claims.get(reward_id, 0) or 0):
        return _success([], {"playerDataDelta": grants.empty_delta()})
    point_key = "weeklyPoint" if mission_type == "WEEKLY" else "dailyPoint"
    current = int(root["missionRewards"].get(point_key, 0) or 0)
    cost = int(reward.get("periodicalPointCost", 0) or 0)
    if current < cost:
        return {"result": 1, "error": "not enough mission points"}
    root["missionRewards"][point_key] = current - cost
    type_claims[reward_id] = 1
    items, delta = grants.apply_items(user, reward.get("rewards") or [], source="mission-point")
    delta["modified"]["mission"] = user.get("mission", {})
    return _success(items, {"playerDataDelta": delta})


def record_stage_clear(user, stage_id, complete_state):
    root = mission_root(user)
    completed = []
    for mission_type, bucket in (root.get("missions") or {}).items():
        if not isinstance(bucket, dict):
            continue
        for mission_id, node in bucket.items():
            if not isinstance(node, dict) or int(node.get("state", 0) or 0) != 1:
                continue
            entry = game_data.mission(mission_id)
            if not entry:
                continue
            template = str(entry.get("template") or "")
            params = [str(value) for value in entry.get("param") or []]
            matched = False
            if template in {"CompleteStage", "CompleteMainStage"} and stage_id in params:
                matched = True
            elif template in {"CompleteAnyStage", "CompleteStageAnyType"}:
                matched = True
            elif template == "CompleteStage" and not params:
                matched = True
            if not matched:
                continue
            progress = node.get("progress")
            if template in {"CompleteStage", "CompleteMainStage"}:
                _complete_progress(node)
            elif isinstance(progress, list) and progress:
                for item in progress:
                    if isinstance(item, dict):
                        item["value"] = int(item.get("value", 0) or 0) + 1
            else:
                node["progress"] = [{"value": 1, "target": 1}]
            done = True
            for item in node.get("progress") or []:
                if isinstance(item, dict) and int(item.get("value", 0) or 0) < int(item.get("target", 1) or 1):
                    done = False
                    break
            if template in {"CompleteStage", "CompleteMainStage"} or done:
                node["state"] = 2
                completed.append(mission_id)
    return completed
