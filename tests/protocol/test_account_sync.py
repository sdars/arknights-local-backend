import json
import sqlite3
import sys
import time
from pathlib import Path

from conftest import DB_PATH

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app import player_delta


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
    first = client.post(
        f"{client.base_url}/account/syncStatus", headers=login["headers"], json={}, timeout=10
    ).json()
    assert isinstance(first["result"], dict)
    assert isinstance(first["ts"], int)
    assert first["pushMessage"] == []
    second = client.post(
        f"{client.base_url}/account/syncStatus", headers=login["headers"], json={}, timeout=10
    ).json()
    assert second["playerDataDelta"] == {"deleted": {}, "modified": {}}
    assert len(second["playerDataDelta"]["modified"]) == 0


def test_sync_status_reports_only_direct_server_changes(client, login, test_account):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(test_account["uid"]),)).fetchone()
        user = json.loads(row[0])
        expected = int(user["status"].get("gold", 0)) + 37
        user["status"]["gold"] = expected
        conn.execute(
            "UPDATE accounts SET user_json=? WHERE uid=?",
            (json.dumps(user, ensure_ascii=False, separators=(",", ":")), int(test_account["uid"])),
        )
    body = client.post(
        f"{client.base_url}/account/syncStatus", headers=login["headers"], json={}, timeout=10
    ).json()
    modified = body["playerDataDelta"]["modified"]
    assert modified["status"]["gold"] == expected
    assert "dungeon" not in modified
    assert "mission" not in modified


def test_sync_delta_ignores_same_day_bookkeeping_but_exposes_day_rollover():
    now = int(time.time())
    same_day = player_delta.sync_delta(
        {"status": {"lastOnlineTs": now - 60, "lastRefreshTs": now - 60}},
        {"status": {"lastOnlineTs": now, "lastRefreshTs": now}},
    )
    assert same_day == {"modified": {}, "deleted": {}}
    rollover = player_delta.sync_delta(
        {"status": {"lastOnlineTs": now - 172800, "lastRefreshTs": now - 172800}},
        {"status": {"lastOnlineTs": now, "lastRefreshTs": now}},
    )
    assert rollover["modified"]["status"]["lastRefreshTs"] == now


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
