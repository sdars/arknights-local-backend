import json
import sqlite3
import sys
import time
from pathlib import Path

from conftest import DB_PATH


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import missions  # noqa: E402


def _template():
    return json.loads((ROOT / "backend" / "data" / "player_template.json").read_text(encoding="utf-8"))


def _user(uid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()
    return json.loads(row[0])


def _save_user(uid, user):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE accounts SET user_json=? WHERE uid=?",
            (json.dumps(user, ensure_ascii=False), int(uid)),
        )


def _complete(user, mission_type, mission_id):
    node = user["mission"]["missions"][mission_type][mission_id]
    node["state"] = 2
    for item in node.get("progress") or []:
        if isinstance(item, dict):
            item["value"] = int(item.get("target", 1) or 1)
    return node


def _freeze_periodic_reset(user, now=None):
    now = int(now or time.time())
    user["mission"]["lastDailyReset"] = missions._reset_timestamp(now)
    user["mission"]["lastWeeklyReset"] = missions._weekly_reset_timestamp(now)


def _daily_ids():
    return missions.current_daily_mission_ids()


def test_confirm_mission_grants_and_persists(client, login, test_account):
    before = _user(test_account["uid"])
    before_cards = int(before.get("inventory", {}).get("2001", 0) or 0)
    stage = before.setdefault("dungeon", {}).setdefault("stages", {}).setdefault("main_00-01", {})
    stage.update({"state": 3, "completeTimes": max(1, int(stage.get("completeTimes", 0) or 0))})
    _complete(before, "MAIN", "main_1")
    _save_user(test_account["uid"], before)

    response = client.post(
        f"{client.base_url}/mission/confirmMission",
        headers=login["headers"],
        json={"missionId": "main_1"},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == 0
    assert body["items"] == [{"id": "2001", "type": "CARD_EXP", "count": 5}]
    node = body["playerDataDelta"]["modified"]["mission"]["missions"]["MAIN"]["main_1"]
    assert node["state"] == 3
    assert body["playerDataDelta"]["modified"]["inventory"]["2001"] == before_cards + 5

    after = _user(test_account["uid"])
    assert after["mission"]["missions"]["MAIN"]["main_1"]["state"] == 3
    assert after["mission"]["missions"]["MAIN"]["main_2"]["state"] == 2
    assert after["inventory"].get("2001", 0) == before_cards + 5


def test_confirm_mission_is_idempotent(client, login, test_account):
    before = _user(test_account["uid"])
    response = client.post(
        f"{client.base_url}/mission/confirmMission",
        headers=login["headers"],
        json={"missionId": "main_1"},
        timeout=15,
    ).json()
    assert response["result"] == 0
    assert response["items"] == []
    after = _user(test_account["uid"])
    assert after["inventory"].get("2001", 0) == before["inventory"].get("2001", 0)


def test_in_progress_mission_cannot_be_claimed(client, login, test_account):
    daily_id = _daily_ids()[1]
    before = _user(test_account["uid"])
    node = before["mission"]["missions"]["DAILY"][daily_id]
    node["state"] = 1
    node["progress"] = [{"value": 1, "target": 2}]
    _save_user(test_account["uid"], before)
    assert node["state"] == 1
    before_cards = int(before.get("inventory", {}).get("2001", 0) or 0)

    response = client.post(
        f"{client.base_url}/mission/confirmMission",
        headers=login["headers"],
        json={"missionId": daily_id},
        timeout=15,
    ).json()
    assert response["result"] == 0
    assert response["claimed"] == []
    assert response["skipped"][0]["reason"] == "not-completed"
    assert response["items"] == []

    after = _user(test_account["uid"])
    assert after["mission"]["missions"]["DAILY"][daily_id]["state"] == 1
    assert after["inventory"].get("2001", 0) == before_cards


def test_mission_repair_keeps_confirmed_rows_visible():
    user = _template()
    _freeze_periodic_reset(user)
    daily_id = _daily_ids()[0]
    daily = user["mission"]["missions"]["DAILY"]
    daily[daily_id] = {"state": 2, "progress": [{"value": 0, "target": 2}]}
    missions.ensure_mission_state(user)
    assert daily[daily_id]["state"] == 2
    assert daily[daily_id]["progress"] == [{"value": 0, "target": 2}]


def test_mission_repair_clamps_group_state_to_enum_values():
    user = _template()
    user["mission"]["missionGroups"]["guide_g_1"] = 2
    missions.ensure_mission_state(user)
    assert user["mission"]["missionGroups"]["guide_g_1"] == 1


def test_claim_rejects_confirmed_but_incomplete_mission():
    user = _template()
    _freeze_periodic_reset(user)
    daily_id = _daily_ids()[1]
    daily = user["mission"]["missions"]["DAILY"]
    daily[daily_id] = {"state": 2, "progress": [{"value": 0, "target": 2}]}
    before_cards = int(user.get("inventory", {}).get("2001", 0) or 0)
    result = missions.claim_missions(user, [daily_id])
    assert result["claimed"] == []
    assert result["skipped"][0]["reason"] == "progress-incomplete"
    assert result["items"] == []
    assert daily[daily_id]["state"] == 2
    assert int(user.get("inventory", {}).get("2001", 0) or 0) == before_cards


def test_auto_confirm_accepts_numeric_mission_type_enum():
    user = _template()
    _complete(user, "SUB", "sub_1")
    result = missions.auto_confirm(user, 5)
    assert result["result"] == 0
    assert result["claimed"] == ["sub_1"]
    assert user["mission"]["missions"]["SUB"]["sub_1"]["state"] == 3


def test_main_claim_all_also_confirms_visible_sub_missions():
    user = _template()
    _complete(user, "SUB", "sub_1")
    _complete(user, "SUB", "sub_2")
    result = missions.auto_confirm(user, "MAIN")
    assert result["result"] == 0
    assert set(result["claimed"]) >= {"sub_1", "sub_2"}
    assert user["mission"]["missions"]["SUB"]["sub_1"]["state"] == 3
    assert user["mission"]["missions"]["SUB"]["sub_2"]["state"] == 3


def test_claiming_last_sub_mission_does_not_cross_mission_groups():
    user = _template()
    _complete(user, "SUB", "sub_6")
    result = missions.claim_missions(user, ["sub_6"])
    assert result["claimed"] == ["sub_6"]
    assert "sub_7" not in user["mission"]["missions"]["SUB"]


def test_mission_repair_restores_cleared_main_stage():
    user = _template()
    user["mission"]["missions"]["MAIN"]["main_1"] = {
        "state": 1,
        "progress": [{"value": 0, "target": 1}],
    }
    user.setdefault("dungeon", {}).setdefault("stages", {})["main_00-01"] = {
        "state": 3,
        "stageId": "main_00-01",
    }
    missions.ensure_mission_state(user)
    assert user["mission"]["missions"]["MAIN"]["main_1"]["state"] == 2


def test_current_main_mission_stays_visible_before_it_is_complete():
    user = _template()
    missions.ensure_mission_state(user)
    main = user["mission"]["missions"]["MAIN"]
    assert main["main_1"]["state"] == 2
    assert main["main_1"]["progress"] == [{"value": 0, "target": 1}]
    assert "main_2" not in main


def test_stage_clear_progresses_visible_daily_mission():
    user = _template()
    _freeze_periodic_reset(user)
    daily_id = _daily_ids()[1]
    daily = user["mission"]["missions"]["DAILY"]
    daily[daily_id] = {"state": 2, "progress": [{"value": 0, "target": 2}]}
    missions.record_stage_clear(user, "main_00-01", 3)
    assert daily[daily_id]["progress"][0]["value"] == 1
    assert daily[daily_id]["state"] == 2
    missions.record_stage_clear(user, "main_00-01", 3)
    assert daily[daily_id]["progress"][0]["value"] == 2
    assert daily[daily_id]["state"] == 2


def test_confirm_mission_list_and_group_rewards(client, login, test_account):
    user = _user(test_account["uid"])
    _complete(user, "SUB", "sub_1")
    for mission_id in user["mission"]["missions"]["GUIDE"]:
        _complete(user, "GUIDE", mission_id)
    _save_user(test_account["uid"], user)
    listed = client.post(
        f"{client.base_url}/mission/confirmMissionList",
        headers=login["headers"],
        json={"missionIds": ["sub_1", "guide_1"]},
        timeout=15,
    ).json()
    assert listed["result"] == 0
    assert set(listed["claimed"]) == {"sub_1", "guide_1"}
    assert listed["items"] == [{"id": "2001", "type": "CARD_EXP", "count": 10}]

    auto = client.post(
        f"{client.base_url}/mission/autoConfirmMissions",
        headers=login["headers"],
        json={"type": 4},
        timeout=15,
    ).json()
    assert auto["result"] == 0
    assert auto["claimed"]

    group = client.post(
        f"{client.base_url}/mission/confirmMissionGroup",
        headers=login["headers"],
        json={"missionGroupId": "guide_g_1"},
        timeout=15,
    ).json()
    assert group["result"] == 0
    assert group["items"] == [{"id": "7003", "type": "TKT_GACHA", "count": 1}]
    user = _user(test_account["uid"])
    assert user["mission"]["missionGroups"]["guide_g_1"] == 1
    repeated = client.post(
        f"{client.base_url}/mission/confirmMissionGroup",
        headers=login["headers"],
        json={"missionGroupId": "guide_g_1"},
        timeout=15,
    ).json()
    assert repeated["result"] == 0
    assert repeated["items"] == []


def test_exchange_mission_point_reward(client, login, test_account):
    user = _user(test_account["uid"])
    user["mission"]["missionRewards"]["dailyPoint"] = 20
    user["mission"]["missionRewards"]["rewards"].setdefault("DAILY", {})[
        "reward_daily_692"
    ] = 0
    _save_user(test_account["uid"], user)
    user = _user(test_account["uid"])
    before_gold = user["status"]["gold"]
    response = client.post(
        f"{client.base_url}/mission/exchangeMissionRewards",
        headers=login["headers"],
        json={"targetRewardsId": "reward_daily_692"},
        timeout=15,
    ).json()
    assert response["result"] == 0
    assert response["items"] == [{"id": "4001", "type": "GOLD", "count": 500}]
    assert _user(test_account["uid"])["status"]["gold"] == before_gold + 500


def _at(year, month, day, hour=12):
    return int(time.mktime((year, month, day, hour, 0, 0, 0, 0, -1)))


def test_daily_reset_switches_to_weekday_group_and_activates_one_tier_per_family():
    user = _template()
    now = _at(2026, 11, 24)
    missions.reset_periodic_missions(user, now)
    daily = user["mission"]["missions"]["DAILY"]
    assert set(daily) == set(missions.current_daily_mission_ids(now))
    assert list(daily)[:3] == ["daily_7001", "daily_7002", "daily_7003"]
    active = [mission_id for mission_id, node in daily.items() if node["state"] == 2]
    assert active == missions._active_mission_ids(list(daily))
    assert len(active) == 13
    assert daily["daily_7001"]["state"] == 2
    assert daily["daily_7002"]["state"] == 1
    assert user["mission"]["missionGroups"]["daily_g_70"] == 0
    assert user["mission"]["missionRewards"]["dailyPoint"] == 0
    assert set(user["mission"]["missionRewards"]["rewards"]["DAILY"]) == {
        f"reward_daily_{number}" for number in range(682, 692)
    }


def test_daily_reset_switches_to_weekend_group():
    user = _template()
    now = _at(2026, 11, 28)
    missions.reset_periodic_missions(user, now)
    daily = user["mission"]["missions"]["DAILY"]
    assert list(daily)[:3] == ["daily_7101", "daily_7102", "daily_7103"]
    assert user["mission"]["missionGroups"]["daily_g_71"] == 0
    assert sum(1 for node in daily.values() if node["state"] == 2) == 13


def test_weekly_reset_activates_one_tier_per_family_and_clears_points():
    user = _template()
    user["mission"]["missionRewards"]["weeklyPoint"] = 12
    user["mission"]["missionRewards"]["rewards"]["WEEKLY"]["reward_weekly_401"] = 1
    now = _at(2026, 11, 24)
    missions.reset_periodic_missions(user, now)
    weekly = user["mission"]["missions"]["WEEKLY"]
    assert set(weekly) == set(missions.current_weekly_mission_ids(now))
    assert sum(1 for node in weekly.values() if node["state"] == 2) == 13
    assert user["mission"]["missionGroups"]["weekly_g_7"] == 0
    assert user["mission"]["missionRewards"]["weeklyPoint"] == 0
    assert user["mission"]["missionRewards"]["rewards"]["WEEKLY"]["reward_weekly_401"] == 0
    assert user["mission"]["lastWeeklyReset"] == missions._weekly_reset_timestamp(now)


def test_claiming_daily_tier_activates_next_tier_in_same_family():
    user = _template()
    now = _at(2026, 11, 24)
    missions.reset_periodic_missions(user, now)
    daily = user["mission"]["missions"]["DAILY"]
    daily["daily_7001"]["progress"][0]["value"] = 1
    result = missions.claim_missions(user, ["daily_7001"])
    assert result["claimed"] == ["daily_7001"]
    assert daily["daily_7001"]["state"] == 3
    assert daily["daily_7002"]["state"] == 2
    assert daily["daily_7002"]["progress"] == [{"value": 0, "target": 2}]
    assert daily["daily_7006"]["state"] == 2


def test_future_daily_tier_does_not_accumulate_progress():
    user = _template()
    now = _at(2026, 11, 24)
    missions.reset_periodic_missions(user, now)
    daily = user["mission"]["missions"]["DAILY"]
    before = dict(daily["daily_7002"])
    missions.record_stage_clear(user, "main_00-01", 3)
    assert daily["daily_7002"] == before
    assert daily["daily_7001"]["progress"][0]["value"] == 1


def test_daily_claim_all_advances_each_family_and_awards_points():
    user = _template()
    now = _at(2026, 11, 24)
    missions.reset_periodic_missions(user, now)
    daily = user["mission"]["missions"]["DAILY"]
    daily["daily_7001"]["progress"][0]["value"] = 1
    daily["daily_7006"]["progress"][0]["value"] = 1
    before_point = user["mission"]["missionRewards"]["dailyPoint"]
    result = missions.auto_confirm(user, "DAILY")
    assert set(result["claimed"]) == {"daily_7001", "daily_7006"}
    assert daily["daily_7001"]["state"] == 3
    assert daily["daily_7006"]["state"] == 3
    assert daily["daily_7002"]["state"] == 2
    assert daily["daily_7007"]["state"] == 2
    assert user["mission"]["missionRewards"]["dailyPoint"] == before_point + 2
    assert "mission" in result["playerDataDelta"]["modified"]


def test_guide_v3_progress_is_derived_from_stage_and_operator_data():
    user = _template()
    user.setdefault("dungeon", {}).setdefault("stages", {})["main_00-01"] = {
        "state": 3,
        "completeTimes": 1,
        "stageId": "main_00-01",
    }
    for char in (user.get("troop") or {}).get("chars", {}).values():
        char["level"] = 1
    first = next(iter((user["troop"]["chars"]).values()))
    first["level"] = 20
    missions.ensure_mission_state(user)
    guide = user["mission"]["missions"]["GUIDE_V3"]
    assert guide["newguide_1_1"]["state"] == 2
    assert guide["newguide_1_1"]["progress"] == [{"value": 1, "target": 1}]
    assert guide["newguide_1_5"]["state"] == 2
    assert guide["newguide_1_5"]["progress"] == [{"value": 1, "target": 1}]
    assert guide["newguide_1_6"]["state"] == 1
    assert guide["newguide_1_6"]["progress"] == [{"value": 1, "target": 3}]


def test_guide_v3_claim_grants_and_marks_finished():
    user = _template()
    user.setdefault("dungeon", {}).setdefault("stages", {})["main_00-01"] = {
        "state": 3,
        "completeTimes": 1,
        "stageId": "main_00-01",
    }
    missions.ensure_mission_state(user)
    result = missions.claim_missions(user, ["newguide_1_1"])
    assert result["claimed"] == ["newguide_1_1"]
    assert user["mission"]["missions"]["GUIDE_V3"]["newguide_1_1"]["state"] == 3
    assert any(item["type"] == "OPEN_SERVER_CREDIT" for item in result["items"])


def test_guide_v3_derives_e1_shop_building_and_challenge_conditions():
    user = _template()
    chars = user.setdefault("troop", {}).setdefault("chars", {})
    for char in chars.values():
        char["level"] = 1
        char["evolvePhase"] = 0
        char["mainSkillLvl"] = 1
        char["skills"] = []
    first = next(iter(chars.values()))
    first.update({"level": 90, "evolvePhase": 2})
    user.setdefault("shop", {}).setdefault("GP", {})["monthly"] = {
        "info": [{"id": "GP_gM_96_M_1", "count": 1}],
    }
    rooms = user.setdefault("building", {}).setdefault("roomSlots", {})
    rooms["slot_34"] = {"roomId": "CONTROL", "level": 4}
    stages = user.setdefault("dungeon", {}).setdefault("stages", {})
    stages["main_00-01"] = {"state": 3, "completeTimes": 1, "stageId": "main_00-01"}

    missions.ensure_mission_state(user)
    guide = user["mission"]["missions"]["GUIDE_V3"]
    assert guide["newguide_4_7"]["state"] == 2
    assert guide["newguide_5_6"]["state"] == 2
    assert guide["newguide_6_5"]["state"] == 2
    assert guide["newguide_2_4"]["state"] == 2
    assert guide["newguide_3_3"]["state"] == 2
    assert guide["newguide_6_1"]["state"] == 2
    # A normal clear must not satisfy the challenge-mode task.
    assert guide["newguide_5_3"]["state"] == 1

    stages["main_00-01#f#"] = {
        "state": 3,
        "completeTimes": 1,
        "stageId": "main_00-01#f#",
    }
    missions.ensure_mission_state(user)
    assert user["mission"]["missions"]["GUIDE_V3"]["newguide_5_3"]["state"] == 2


def test_guide_auto_confirm_only_claims_current_stage():
    user = _template()
    chars = user.setdefault("troop", {}).setdefault("chars", {})
    for char in chars.values():
        char["level"] = 1
        char["evolvePhase"] = 0
    first = next(iter(chars.values()))
    first["level"] = 60
    user.setdefault("dungeon", {}).setdefault("stages", {})["main_00-01"] = {
        "state": 3,
        "completeTimes": 1,
        "stageId": "main_00-01",
    }

    result = missions.auto_confirm(user, "GUIDE_V3")
    guide = user["mission"]["missions"]["GUIDE_V3"]
    assert "newguide_1_1" in result["claimed"]
    assert "newguide_2_6" not in result["claimed"]
    assert guide["newguide_2_6"]["state"] == 2
