import json
import sqlite3
import time

from conftest import DB_PATH


def test_battle_start(client, login):
    body = client.post(
        f"{client.base_url}/quest/battleStart",
        headers=login["headers"],
        json={
            "isRetro": 0,
            "pry": 0,
            "battleType": 0,
            "multiple": None,
            "extra": None,
            "usePracticeTicket": 0,
            "stageId": "guide_01",
            "squad": None,
            "assistFriend": None,
            "isReplay": 0,
            "startTs": int(time.time()),
        },
        timeout=15,
    ).json()
    assert body["result"] == 0
    assert body.get("battleId")
    assert body["playerDataDelta"]["modified"]["dungeon"]["stages"]["guide_01"]["stageId"] == "guide_01"


def test_battle_finish(client, login):
    started = client.post(
        f"{client.base_url}/quest/battleStart",
        headers=login["headers"],
        json={"stageId": "main_00-01"},
        timeout=15,
    ).json()
    assert started["result"] == 0
    body = client.post(
        f"{client.base_url}/quest/battleFinish",
        headers=login["headers"],
        json={"battleId": started["battleId"], "result": 0, "stats": {}},
        timeout=15,
    ).json()
    assert body["result"] == 0
    stage = body["playerDataDelta"]["modified"]["dungeon"]["stages"]["main_00-01"]
    assert stage["state"] == 3
    assert stage["completeTimes"] == 1
    assert body["playerDataDelta"]["modified"]["status"]["gold"] >= 120
    assert any(item["type"] == "DIAMOND" for item in body["firstRewards"])

    synced = client.post(
        f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=20
    ).json()
    user = synced["user"]
    assert user["dungeon"]["stages"]["main_00-01"]["state"] == 3
    assert user["dungeon"]["stages"]["main_00-01"]["completeTimes"] == 1
    assert user["status"]["androidDiamond"] >= 1
    assert user["inventory"]["2001"] >= 2


def test_battle_rewards_are_persisted_in_sqlite(client, login, test_account):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(test_account["uid"]),)).fetchone()
    user = json.loads(row[0])
    assert user["dungeon"]["stages"]["main_00-01"]["state"] == 3


def test_unknown_route_returns_empty_delta(client, login):
    body = client.post(
        f"{client.base_url}/protocol/test/route-not-in-capture", headers=login["headers"], json={}, timeout=10
    ).json()
    assert body["result"] == 0
    assert body["playerDataDelta"] == {"deleted": {}, "modified": {}}
