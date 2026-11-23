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


def _progress_done(node):
    progress = node.get("progress")
    if not isinstance(progress, list) or not progress:
        return False
    seen = False
    for item in progress:
        if not isinstance(item, dict):
            continue
        seen = True
        target = max(1, int(item.get("target", 1) or 1))
        if int(item.get("value", 0) or 0) < target:
            return False
    return seen


def ensure_mission_state(user):
    """Repair legacy/template mission rows before the client sees them.

    IN_EFFECT (1) renders an unfinished mission with its progress bar;
    CONFIRMED (2) renders the claim button.  Keep those two states aligned with
    the stored progress so incomplete rows cannot advertise a bogus claim.
    """
    root = mission_root(user)
    for bucket in (root.get("missions") or {}).values():
        if not isinstance(bucket, dict):
            continue
        for node in bucket.values():
            if not isinstance(node, dict):
                continue
            progress = node.get("progress")
            if not isinstance(progress, list) or not progress:
                node["progress"] = [{"value": 0, "target": 1}]
                progress = node["progress"]
            state = int(node.get("state", 0) or 0)
            done = _progress_done(node)
            if state == 3:
                continue
            if state in (1, 2):
                node["state"] = 2 if done else 1
            elif state not in (0, 1, 2):
                node["state"] = 1
    # The template is also used by accounts that already cleared story
    # stages.  Restore the one-shot CompleteStage/CompleteMainStage missions
    # from the authoritative dungeon clear flags instead of leaving them at
    # IN_EFFECT after the template repair.
    stages = (user.get("dungeon") or {}).get("stages") or {}
    if isinstance(stages, dict):
        for bucket in (root.get("missions") or {}).values():
            if not isinstance(bucket, dict):
                continue
            for mission_id, node in bucket.items():
                if not isinstance(node, dict):
                    continue
                entry = game_data.mission(mission_id) or {}
                template = str(entry.get("template") or "")
                if template not in {"CompleteStage", "CompleteMainStage"}:
                    continue
                params = [str(value) for value in entry.get("param") or []]
                stage_id = next((value for value in params if value in stages), "")
                if not stage_id:
                    continue
                stage = stages.get(stage_id)
                if not isinstance(stage, dict) or int(stage.get("state", 0) or 0) < 3:
                    continue
                progress = node.get("progress")
                if isinstance(progress, list):
                    for item in progress:
                        if isinstance(item, dict):
                            item["value"] = max(
                                int(item.get("value", 0) or 0),
                                int(item.get("target", 1) or 1),
                            )
                if int(node.get("state", 0) or 0) != 3:
                    node["state"] = 2
    ensure_main_mission_chain(user)


def _mission_sort_key(mission_id):
    match = re.search(r"(\d+)$", str(mission_id or ""))
    return (int(match.group(1)) if match else 10**9, str(mission_id))


def ensure_main_mission_chain(user):
    """Publish the current MAIN mission instead of leaving only main_1 in the save.

    The terminal card renders the current main mission; a save containing only
    a completed main_1 makes it say "全部完成" even though later chapters are
    still locked.
    """
    table = (game_data.mission_table() or {}).get("missions") or {}
    entries = [
        (str(mission_id), entry)
        for mission_id, entry in table.items()
        if isinstance(entry, dict) and str(entry.get("type") or "").upper() == "MAIN"
    ]
    entries.sort(key=lambda row: _mission_sort_key(row[0]))
    bucket = mission_bucket(user, "MAIN", create=True)
    stages = ((user.get("dungeon") or {}).get("stages")) or {}
    activated = False
    for mission_id, entry in entries:
        template = str(entry.get("template") or "")
        params = [str(value) for value in entry.get("param") or []]
        stage_id = next((value for value in params if value in stages), "")
        cleared = False
        if stage_id and template in {"CompleteMainStage", "CompleteStage"}:
            node = stages.get(stage_id)
            cleared = isinstance(node, dict) and (
                int(node.get("state", 0) or 0) >= 2
                or int(node.get("completeTimes", 0) or 0) > 0
            )
        existing = bucket.get(mission_id)
        if cleared:
            node = existing if isinstance(existing, dict) else {"state": 2, "progress": []}
            if int(node.get("state", 0) or 0) != 3:
                node["state"] = 2
            node["progress"] = [{"value": 1, "target": 1}]
            bucket[mission_id] = node
            continue
        if isinstance(existing, dict) and int(existing.get("state", 0) or 0) == 3:
            continue
        if activated:
            if isinstance(existing, dict):
                bucket.pop(mission_id, None)
            continue
        node = existing if isinstance(existing, dict) else {"state": 1, "progress": []}
        # The client's MAIN page only includes MissionHoldingState.CONFIRMED
        # rows.  For a main mission that is the visible "current task" state,
        # not necessarily a claimable reward; actual claiming is guarded by the
        # progress target in claim_missions.
        node["state"] = 2
        if template in {"CompleteMainStage", "CompleteStage"}:
            node["progress"] = [{"value": 0, "target": 1}]
        elif not node.get("progress"):
            node["progress"] = [{"value": 0, "target": 1}]
        bucket[mission_id] = node
        activated = True


def _next_mission_id(mission_id, mission_type):
    match = re.match(r"^(.*_)(\d+)$", str(mission_id or ""))
    if not match:
        return None
    candidate = f"{match.group(1)}{int(match.group(2)) + 1}"
    entry = game_data.mission(candidate)
    if not entry or entry.get("type") != mission_type:
        return None
    current = game_data.mission(mission_id) or {}
    current_group = current.get("missionGroup")
    candidate_group = entry.get("missionGroup")
    # SUB chains such as sub_1..sub_6 are one group and end there.  The next
    # numeric id (sub_7) belongs to a different mission set and must not be
    # injected into the current page after claiming the last visible item.
    if mission_type != "MAIN" and current_group and candidate_group:
        if str(current_group) != str(candidate_group):
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
    ensure_mission_state(user)
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
        state = int(node.get("state", 0) or 0)
        if state == 3:
            skipped.append({"missionId": mission_id, "reason": "already-claimed"})
            continue
        if state != 2:
            # 1 = IN_EFFECT (still in progress), 0 = NOT_OPEN.  Only a
            # CONFIRMED mission may be collected; otherwise the client could
            # claim every mission simply because it exists in the save.
            skipped.append(
                {"missionId": mission_id, "reason": "not-completed", "state": state}
            )
            continue
        if not _progress_done(node):
            skipped.append(
                {
                    "missionId": mission_id,
                    "reason": "progress-incomplete",
                    "state": state,
                }
            )
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

    ensure_main_mission_chain(user)
    granted_items, delta = grants.apply_items(user, reward_items, source=source)
    delta["modified"]["mission"] = user.get("mission", {})
    return _success(granted_items, {"playerDataDelta": delta, "claimed": claimed, "skipped": skipped})


def auto_confirm(user, mission_type):
    mission_types = {
        1: "MAIN",
        2: "DAILY",
        3: "WEEKLY",
        4: "GUIDE",
        5: "SUB",
        6: "ACTIVITY",
        7: "OPENSERVER",
        8: "TOWERSEASON",
        9: "RETRO",
        10: "SPECIAL_OPERATOR",
        11: "SPECIAL_OPERATOR_WEEKLY",
        12: "GUIDE_V3",
    }
    # AutoConfirmMissionsRequest.type is a MissionType enum.  Capture shows
    # this client sending "MAIN"; other clients serialize the same field as an
    # integer, so accept both forms instead of looking up the string "5".
    if isinstance(mission_type, bool):
        mission_type = "MAIN"
    elif isinstance(mission_type, int) or str(mission_type or "").isdigit():
        mission_type = mission_types.get(int(mission_type), "")
    else:
        mission_type = str(mission_type or "MAIN").upper()
    # The MAIN page contains both MAIN and SUB missions, and its claim-all
    # button sends only MissionType.MAIN.  Confirm both buckets so the visible
    # SUB rows are actually claimed instead of returning result=0/claimed=[].
    bucket_types = [mission_type]
    if mission_type == "MAIN":
        bucket_types.append("SUB")
    elif mission_type == "GUIDE":
        bucket_types.append("GUIDE_V3")
    ids = []
    for bucket_type in bucket_types:
        bucket = mission_bucket(user, bucket_type, create=False)
        ids.extend(
            mission_id
            for mission_id, node in bucket.items()
            if isinstance(node, dict) and int(node.get("state", 0) or 0) == 2
        )
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


def record_stage_clear(user, stage_id, complete_state, kill_count=0, assist=False):
    root = mission_root(user)
    completed = []
    for mission_type, bucket in (root.get("missions") or {}).items():
        if not isinstance(bucket, dict):
            continue
        for mission_id, node in bucket.items():
            if not isinstance(node, dict):
                continue
            state = int(node.get("state", 0) or 0)
            if state not in (1, 2):
                continue
            entry = game_data.mission(mission_id)
            if not entry:
                continue
            template = str(entry.get("template") or "")
            params = [str(value) for value in entry.get("param") or []]
            matched = False
            increment = 0
            complete = False
            if template in {"CompleteStage", "CompleteMainStage"} and stage_id in params:
                matched = True
                complete = True
            elif template in {"CompleteAnyStage", "CompleteStageAnyType"}:
                matched = True
                increment = 1
            elif template == "CompleteStage" and not params:
                matched = True
                complete = True
            elif template == "StageWithEnemyKill" and int(kill_count or 0) > 0:
                matched = True
                increment = 1
            elif template == "EnemyKillInAnyStage" and int(kill_count or 0) > 0:
                matched = True
                increment = int(kill_count or 0)
            elif template == "StageWithAssistChar" and assist:
                matched = True
                increment = 1
            if not matched:
                continue
            progress = node.get("progress")
            if complete:
                _complete_progress(node)
            elif isinstance(progress, list) and progress:
                for item in progress:
                    if isinstance(item, dict):
                        item["value"] = int(item.get("value", 0) or 0) + increment
            else:
                node["progress"] = [{"value": increment, "target": 1}]
            done = True
            for item in node.get("progress") or []:
                if isinstance(item, dict) and int(item.get("value", 0) or 0) < int(item.get("target", 1) or 1):
                    done = False
                    break
            if complete or done:
                node["state"] = 2
                completed.append(mission_id)
            elif int(node.get("state", 0) or 0) != 3:
                node["state"] = 1
    return completed
