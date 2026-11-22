import json
import sqlite3
import time

from conftest import DB_PATH


def test_account_login(client, login, test_account):
    body = login["body"]
    assert body["uid"] == int(test_account["uid"])
    assert body["data"]["result"] == 0
    assert body["serviceLicenseVersion"] == 0


def test_sync_data(client, login, test_account):
    response = client.post(
        f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=20
    )
    body = response.json()
    assert response.status_code == 200
    assert body["result"] == 0
    assert body["user"]["status"]["uid"] == test_account["uid"]
    now = int(time.time())
    assert abs(int(body["ts"]) - now) < 30
    assert body["user"]["status"]["lastOnlineTs"] == body["ts"]
    assert body["user"]["status"]["lastRefreshTs"] == body["ts"]
    assert len(json.dumps(body["user"])) > 100_000


def test_sync_status(client, login):
    body = client.post(
        f"{client.base_url}/account/syncStatus", headers=login["headers"], json={}, timeout=10
    ).json()
    assert body["result"] == 0
    assert body["pushMessage"] == []


def test_sync_data_persists_last_refresh_ts(client, login, test_account):
    first = client.post(
        f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=20
    ).json()
    assert first["result"] == 0
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(test_account["uid"]),)).fetchone()
    assert row is not None
    user = json.loads(row[0])
    assert user["status"]["lastRefreshTs"] == first["ts"]
    assert user["status"]["lastOnlineTs"] == first["ts"]


def test_sync_data_rejects_bad_secret(client):
    body = client.post(
        f"{client.base_url}/account/syncData",
        headers={"secret": "not-a-valid-token", "uid": "10000000"},
        json={},
        timeout=10,
    ).json()
    assert body["result"] == 2
