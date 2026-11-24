import json
import sqlite3

from conftest import DB_PATH


def _user(uid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()
    return json.loads(row[0])


def test_reward_only_grants_pool_character_and_persists(client, login, test_account):
    response = client.post(
        f"{client.base_url}/activity/actRewardOnly/getReward",
        headers=login["headers"],
        json={"activityId": "act1reward", "poolId": "pool_1"},
        timeout=15,
    ).json()
    assert response["result"] == 0
    char = response["charGet"]
    assert char["charId"].startswith("char_")
    assert char["charInstId"] > 0
    assert "activity" in response["playerDataDelta"]["modified"]

    user = _user(test_account["uid"])
    state = user["activity"]["REWARD_ONLY"]["act1reward"]
    assert state == {"hit": char["charId"], "choice": "pool_1"}
    assert str(char["charInstId"]) in user["troop"]["chars"]

    repeated = client.post(
        f"{client.base_url}/activity/actRewardOnly/getReward",
        headers=login["headers"],
        json={"activityId": "act1reward", "poolId": "pool_2"},
        timeout=15,
    ).json()
    assert repeated["result"] == 1
    assert repeated["error"] == "reward already claimed"
