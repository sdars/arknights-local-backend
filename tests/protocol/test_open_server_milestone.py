import json
import sqlite3

from conftest import DB_PATH


def _user(uid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()
    return json.loads(row[0])


def _save_user(uid, user):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE accounts SET user_json=? WHERE uid=?",
            (json.dumps(user, ensure_ascii=False, separators=(",", ":")), int(uid)),
        )


def _reset(uid, point=400):
    user = _user(uid)
    user.setdefault("openServerV3", {})["mission"] = {
        "milestone": {"groupId": "new_bp_g_1", "point": point, "got": []}
    }
    user.setdefault("status", {})["gold"] = 10000
    user.setdefault("inventory", {})["2001"] = 0
    _save_user(uid, user)
    return user


def test_milestone_reward_claims_all_available_levels(client, login, test_account):
    _reset(test_account["uid"])
    response = client.post(
        f"{client.base_url}/openServerV3/getMilestoneReward",
        headers=login["headers"],
        json={"levels": [1, 2, 3, 4, 5]},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == 0
    assert body["items"] == [
        {"id": "2001", "type": "CARD_EXP", "count": 30},
        {"id": "4001", "type": "GOLD", "count": 6000},
        {"id": "itempack_material_t1", "type": "ITEM_PACK", "count": 5},
    ]
    after = _user(test_account["uid"])
    milestone = after["openServerV3"]["mission"]["milestone"]
    assert milestone["got"] == [1, 2, 3, 4, 5]
    assert after["status"]["gold"] == 16000
    assert after["inventory"]["2001"] == 30

    duplicate = client.post(
        f"{client.base_url}/openServerV3/getMilestoneReward",
        headers=login["headers"],
        json={"levels": [1, 2, 3, 4, 5]},
        timeout=15,
    ).json()
    assert duplicate["result"] == 0
    assert duplicate["items"] == []
    assert _user(test_account["uid"])["status"]["gold"] == 16000


def test_milestone_reward_rejects_locked_level(client, login, test_account):
    _reset(test_account["uid"])
    body = client.post(
        f"{client.base_url}/openServerV3/getMilestoneReward",
        headers=login["headers"],
        json={"levels": [6]},
        timeout=15,
    ).json()
    assert body["result"] == 0
    assert body["items"] == []
    assert _user(test_account["uid"])["openServerV3"]["mission"]["milestone"]["got"] == []
