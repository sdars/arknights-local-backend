import base64
import io
import json
import os
import sqlite3
import time
import zipfile

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


def test_battle_finish(client, login, test_account):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT user_json FROM accounts WHERE uid=?", (int(test_account["uid"]),)
        ).fetchone()
        user = json.loads(row[0])
        user.setdefault("status", {})["mainStageProgress"] = "main_00-01"
        user.setdefault("dungeon", {}).setdefault("stages", {})["main_00-01"] = {
            "hasBattleReplay": 0,
            "practiceTimes": 0,
            "state": 0,
            "completeTimes": 0,
            "stageId": "main_00-01",
            "startTimes": 0,
        }
        conn.execute(
            "UPDATE accounts SET user_json=? WHERE uid=?",
            (json.dumps(user, ensure_ascii=False, separators=(",", ":")), int(test_account["uid"])),
        )
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
    assert body["itemReturn"] == []
    stage = body["playerDataDelta"]["modified"]["dungeon"]["stages"]["main_00-01"]
    assert stage["state"] == 3
    assert stage["completeTimes"] >= 1
    assert body["playerDataDelta"]["modified"]["status"]["gold"] >= 120
    assert body["nextStage"] == "main_00-02"
    assert body["playerDataDelta"]["modified"]["status"]["mainStageProgress"] == "main_00-02"
    assert any(item["type"] == "DIAMOND" for item in body["firstRewards"])

    synced = client.post(
        f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=20
    ).json()
    user = synced["user"]
    assert user["dungeon"]["stages"]["main_00-01"]["state"] == 3
    assert user["dungeon"]["stages"]["main_00-01"]["completeTimes"] >= 1
    assert user["status"]["androidDiamond"] >= 1
    assert user["inventory"]["2001"] >= 2


def test_battle_rewards_are_persisted_in_sqlite(client, login, test_account):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(test_account["uid"]),)).fetchone()
    user = json.loads(row[0])
    assert user["dungeon"]["stages"]["main_00-01"]["state"] == 3


def test_battle_finish_records_uploaded_payload(client, login, test_account):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM battle_records WHERE uid=? ORDER BY id DESC LIMIT 1",
            (int(test_account["uid"]),),
        ).fetchone()
    assert row is not None
    record = dict(row)
    assert record["stage_id"] == "main_00-01"
    assert record["stage_code"] == "0-1"
    assert record["result"] == "win"
    assert record["complete_state"] >= 2
    payload = json.loads(record["payload_json"])
    assert payload.get("_battleStart", {}).get("stageId") == "main_00-01"
    assert payload.get("_battleFinish", {}).get("battleId") == record["battle_id"]
    raw = json.loads(record["raw_data"])
    assert raw.get("battleId") == record["battle_id"]


def test_admin_battle_records_show_uploaded_payload(client, test_account):
    login = client.post(
        f"{client.base_url}/admin/login",
        data={
            "username": os.environ.get("ARK_ADMIN_USER", "sdars"),
            "password": os.environ.get("ARK_ADMIN_PASSWORD", "123456"),
        },
        allow_redirects=False,
        timeout=10,
    )
    assert login.status_code == 303
    page = client.get(
        f"{client.base_url}/admin/battles",
        params={"uid": test_account["uid"], "stage": "0-1"},
        timeout=15,
    )
    assert page.status_code == 200
    assert "main_00-01" in page.text
    assert "完整 battleFinish 请求体" in page.text
    assert "_battleStart" in page.text


def test_unknown_route_returns_empty_delta(client, login):
    body = client.post(
        f"{client.base_url}/protocol/test/route-not-in-capture", headers=login["headers"], json={}, timeout=10
    ).json()
    assert body["result"] == 0
    assert body["playerDataDelta"] == {"deleted": {}, "modified": {}}


def test_battle_replay_round_trip(client, login):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "journal.json",
            json.dumps({"journal": {"metadata": {"stageId": "main_00-01"}}}),
        )
    replay = base64.b64encode(buffer.getvalue()).decode("ascii")
    saved = client.post(
        f"{client.base_url}/quest/saveBattleReplay",
        headers=login["headers"],
        json={"battleReplay": replay},
        timeout=15,
    ).json()
    assert saved["result"] == 0, saved
    assert saved["stageId"] == "main_00-01"
    loaded = client.post(
        f"{client.base_url}/quest/getBattleReplay",
        headers=login["headers"],
        json={"stageId": "main_00-01"},
        timeout=15,
    ).json()
    assert loaded["result"] == 0, loaded
    assert loaded["battleReplay"] == replay
