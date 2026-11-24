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


def _reset_open_server(uid):
    user = _user(uid)
    user.setdefault("openServerV3", {})["checkIn"] = {
        "groupId": "new_login_g_1",
        "history": [1, 1],
    }
    user.setdefault("checkIn", {})["checkInHistory"] = []
    user.setdefault("status", {})["gold"] = 10000
    _save_user(uid, user)
    return user


def test_open_server_checkin_grants_once_and_persists(client, login, test_account):
    _reset_open_server(test_account["uid"])
    response = client.post(
        f"{client.base_url}/openServerV3/getCheckInReward",
        headers=login["headers"],
        json={"index": 0},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == 0
    assert body["items"] == [{"id": "4001", "type": "GOLD", "count": 20000}]
    assert body["reward"] == {"id": "4001", "type": "GOLD", "count": 20000}
    assert body["playerDataDelta"]["modified"]["openServerV3"]["checkIn"]["history"] == [0, 1]
    assert body["playerDataDelta"]["modified"]["status"]["gold"] == 30000

    after = _user(test_account["uid"])
    assert after["openServerV3"]["checkIn"]["history"] == [0, 1]
    assert after["status"]["gold"] == 30000

    duplicate = client.post(
        f"{client.base_url}/openServerV3/getCheckInReward",
        headers=login["headers"],
        json={"index": 0},
        timeout=15,
    ).json()
    assert duplicate["result"] == 0
    assert duplicate["items"] == []
    assert "reward" not in duplicate
    assert _user(test_account["uid"])["status"]["gold"] == 30000

    sync = client.post(
        f"{client.base_url}/account/syncData",
        headers=login["headers"],
        json={},
        timeout=15,
    ).json()
    assert sync["user"]["openServerV3"]["checkIn"]["history"] == [0, 1]
    assert sync["user"]["status"]["gold"] == 30000


def test_legacy_open_server_checkin_route_and_day_two_inventory(client, login, test_account):
    _reset_open_server(test_account["uid"])
    before_cards = int(_user(test_account["uid"]).get("inventory", {}).get("2003", 0) or 0)
    response = client.post(
        f"{client.base_url}/activity/getOpenServerCheckInReward",
        headers=login["headers"],
        json={"index": 1},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == 0
    assert body["items"] == [{"id": "2003", "type": "CARD_EXP", "count": 20}]
    assert body["playerDataDelta"]["modified"]["openServerV3"]["checkIn"]["history"] == [1, 0]

    after = _user(test_account["uid"])
    assert after["openServerV3"]["checkIn"]["history"] == [1, 0]
    assert after["inventory"]["2003"] == before_cards + 20


def test_sync_backfills_cumulative_days_from_daily_signins(client, login, test_account):
    user = _user(test_account["uid"])
    user.setdefault("openServerV3", {})["checkIn"] = {
        "groupId": "new_login_g_1",
        "history": [0],
    }
    user.setdefault("checkIn", {})["checkInHistory"] = [0, 0, 0]
    _save_user(test_account["uid"], user)

    sync = client.post(
        f"{client.base_url}/account/syncData",
        headers=login["headers"],
        json={},
        timeout=15,
    ).json()
    assert sync["user"]["openServerV3"]["checkIn"]["history"] == [0, 1, 1]
    assert _user(test_account["uid"])["openServerV3"]["checkIn"]["history"] == [0, 1, 1]


def test_negative_index_claims_next_available_day(client, login, test_account):
    user = _user(test_account["uid"])
    user.setdefault("openServerV3", {})["checkIn"] = {
        "groupId": "new_login_g_1",
        "history": [0, 1],
    }
    user.setdefault("checkIn", {})["checkInHistory"] = [0, 0]
    _save_user(test_account["uid"], user)

    body = client.post(
        f"{client.base_url}/openServerV3/getCheckInReward",
        headers=login["headers"],
        json={"index": -1},
        timeout=15,
    ).json()
    assert body["result"] == 0
    assert body["items"] == [{"id": "2003", "type": "CARD_EXP", "count": 20}]
    assert body["playerDataDelta"]["modified"]["openServerV3"]["checkIn"]["history"] == [0, 0]
    assert _user(test_account["uid"])["openServerV3"]["checkIn"]["history"] == [0, 0]

    duplicate = client.post(
        f"{client.base_url}/openServerV3/getCheckInReward",
        headers=login["headers"],
        json={"index": -1},
        timeout=15,
    ).json()
    assert duplicate["items"] == []


def test_out_of_range_checkin_index_never_grants(client, login, test_account):
    _reset_open_server(test_account["uid"])
    body = client.post(
        f"{client.base_url}/openServerV3/getCheckInReward",
        headers=login["headers"],
        json={"index": 99},
        timeout=15,
    ).json()
    assert body["result"] == 0
    assert body["items"] == []
    assert _user(test_account["uid"])["openServerV3"]["checkIn"]["history"] == [1, 1]
