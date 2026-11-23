import json
import sqlite3
import sys
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


def test_confirm_mission_grants_and_persists(client, login, test_account):
    before = _user(test_account["uid"])
    before_cards = int(before.get("inventory", {}).get("2001", 0) or 0)
    assert before["mission"]["missions"]["MAIN"]["main_1"]["state"] == 2
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
    before = _user(test_account["uid"])
    node = before["mission"]["missions"]["DAILY"]["daily_7102"]
    assert node["state"] == 1
    before_cards = int(before.get("inventory", {}).get("2001", 0) or 0)

    response = client.post(
        f"{client.base_url}/mission/confirmMission",
        headers=login["headers"],
        json={"missionId": "daily_7102"},
        timeout=15,
    ).json()
    assert response["result"] == 0
    assert response["claimed"] == []
    assert response["skipped"][0]["reason"] == "not-completed"
    assert response["items"] == []

    after = _user(test_account["uid"])
    assert after["mission"]["missions"]["DAILY"]["daily_7102"]["state"] == 1
    assert after["inventory"].get("2001", 0) == before_cards


def test_mission_repair_keeps_confirmed_rows_visible():
    user = _template()
    daily = user["mission"]["missions"]["DAILY"]
    daily["daily_7102"] = {"state": 2, "progress": [{"value": 0, "target": 2}]}
    daily["daily_7101"] = {"state": 1, "progress": [{"value": 1, "target": 1}]}
    missions.ensure_mission_state(user)
    assert daily["daily_7102"]["state"] == 2
    assert daily["daily_7101"]["state"] == 2


def test_claim_rejects_confirmed_but_incomplete_mission():
    user = _template()
    daily = user["mission"]["missions"]["DAILY"]
    daily["daily_7102"] = {"state": 2, "progress": [{"value": 0, "target": 2}]}
    before_cards = int(user.get("inventory", {}).get("2001", 0) or 0)
    result = missions.claim_missions(user, ["daily_7102"])
    assert result["claimed"] == []
    assert result["skipped"][0]["reason"] == "progress-incomplete"
    assert result["items"] == []
    assert daily["daily_7102"]["state"] == 2
    assert int(user.get("inventory", {}).get("2001", 0) or 0) == before_cards


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


def test_stage_clear_progresses_visible_daily_mission():
    user = _template()
    daily = user["mission"]["missions"]["DAILY"]
    daily["daily_7102"] = {"state": 2, "progress": [{"value": 0, "target": 2}]}
    missions.record_stage_clear(user, "main_00-01", 3)
    assert daily["daily_7102"]["progress"][0]["value"] == 1
    assert daily["daily_7102"]["state"] == 2
    missions.record_stage_clear(user, "main_00-01", 3)
    assert daily["daily_7102"]["progress"][0]["value"] == 2
    assert daily["daily_7102"]["state"] == 2


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
        json={"type": "GUIDE"},
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
    assert user["mission"]["missionGroups"]["guide_g_1"] == 2


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
