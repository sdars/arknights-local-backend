import json
import sqlite3

from conftest import DB_PATH


def _user(uid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()
    return json.loads(row[0])


def test_confirm_mission_grants_and_persists(client, login, test_account):
    before = _user(test_account["uid"])
    before_cards = int(before.get("inventory", {}).get("2001", 0) or 0)
    assert before["mission"]["missions"]["MAIN"]["main_1"]["state"] == 2

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
    assert after["inventory"]["2001"] == before_cards + 5


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
    assert after["inventory"]["2001"] == before["inventory"]["2001"]


def test_confirm_mission_list_and_group_rewards(client, login, test_account):
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
    auto = client.post(
        f"{client.base_url}/mission/autoConfirmMissions",
        headers=login["headers"],
        json={"type": "DAILY"},
        timeout=15,
    ).json()
    assert auto["result"] == 0
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
