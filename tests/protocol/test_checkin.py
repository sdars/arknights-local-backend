import json
import sqlite3

from conftest import DB_PATH


def _user(uid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()
    return json.loads(row[0])


def test_checkin_grants_once_per_day(client, login, test_account):
    before = _user(test_account["uid"])
    before_gold = int(before["status"].get("gold", 0) or 0)
    response = client.post(
        f"{client.base_url}/user/checkIn",
        headers=login["headers"],
        json={},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == 0
    assert body["signInRewards"] == [{"id": "4001", "type": "GOLD", "count": 2000}]
    assert body["playerDataDelta"]["modified"]["checkIn"]["canCheckIn"] == 0

    after = _user(test_account["uid"])
    assert after["status"]["gold"] == before_gold + 2000
    assert after["checkIn"]["canCheckIn"] == 0
    assert after["checkIn"]["checkInHistory"]

    duplicate = client.post(
        f"{client.base_url}/user/checkIn", headers=login["headers"], json={}, timeout=15
    ).json()
    assert duplicate["result"] == 0
    assert duplicate["signInRewards"] == []
    assert _user(test_account["uid"])["status"]["gold"] == before_gold + 2000
