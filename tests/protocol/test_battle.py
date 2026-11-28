import base64
import gzip
import io
import json
import os
import sqlite3
import time
import zipfile
import zlib
import sys
from pathlib import Path

from conftest import DB_PATH

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import battles  # noqa: E402


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


def test_unknown_route_returns_not_found(client, login):
    body = client.post(
        f"{client.base_url}/protocol/test/route-not-in-capture", headers=login["headers"], json={}, timeout=10
    )
    assert body.status_code == 404
    payload = body.json()
    assert payload["result"] == 1
    assert payload["path"] == "/protocol/test/route-not-in-capture"


def test_battle_replay_round_trip(client, login):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("metadata.json", json.dumps({"kind": "metadata"}))
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
    synced = client.post(
        f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=20
    ).json()
    node = synced["user"]["dungeon"]["stages"]["main_00-01"]
    assert node["hasBattleReplay"] == 1
    assert node["battleReplay"] == replay


def test_battle_replay_accepts_compressed_json(client, login):
    cases = [
        ("gzip", gzip.compress(json.dumps({"journal": {"metadata": {"stageId": "main_00-01"}}}).encode("utf-8"))),
        ("zlib", zlib.compress(json.dumps({"journal": {"metadata": {"stageId": "main_00-02"}}}).encode("utf-8"))),
    ]
    for label, raw in cases:
        replay = base64.b64encode(raw).decode("ascii")
        saved = client.post(
            f"{client.base_url}/quest/saveBattleReplay",
            headers=login["headers"],
            json={"battleReplay": replay},
            timeout=15,
        ).json()
        assert saved["result"] == 0, (label, saved)
        synced = client.post(
            f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=20
        ).json()
        node = synced["user"]["dungeon"]["stages"][saved["stageId"]]
        assert node["hasBattleReplay"] == 1, (label, node)


def test_build_0_11_replay_uses_real_tiles_and_level_caps():
    chars = {}
    for index, (char_id, inst_id) in enumerate(
        [
            ("char_264_f12yin", 264),
            ("char_4207_branch", 4207),
            ("char_180_amgoat", 180),
            ("char_4080_lin", 4080),
            ("char_204_platnm", 204),
            ("char_341_sntlla", 341),
            ("char_4228_closur", 4228),
            ("char_4081_warmy", 4081),
        ]
    ):
        chars[str(inst_id)] = {
            "instId": inst_id,
            "charId": char_id,
            "skin": f"{char_id}#1",
            "evolvePhase": 0,
            "level": 1,
            "potentialRank": 0,
            "skills": [{"skillId": f"skill_{index}", "unlock": 1}],
        }
    replay = battles.build_battle_replay({"troop": {"chars": chars}}, "main_00-11", 3)
    assert replay
    raw = base64.b64decode(replay + "=" * (-len(replay) % 4))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        payload = json.loads(archive.read("default_entry"))
    journal = payload["journal"]
    assert journal["metadata"]["stageId"] == "main_00-11"
    assert len(journal["squad"]) == 4
    assert len(journal["logs"]) == 4
    valid_tiles = {
        (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),
        (2, 4), (2, 6), (2, 7), (2, 8), (3, 1), (3, 2), (3, 3), (3, 4),
        (3, 5), (3, 6), (3, 7), (3, 8), (4, 1), (4, 2), (4, 3), (4, 4),
        (4, 5), (4, 6), (4, 7), (4, 8), (5, 1), (5, 2), (5, 3), (5, 4),
        (5, 5), (5, 6), (5, 7), (5, 8), (6, 1), (6, 2), (6, 3), (6, 4),
        (6, 5), (6, 6), (6, 7), (6, 8),
    }
    for log in journal["logs"]:
        assert (log["pos"]["row"], log["pos"]["col"]) in valid_tiles
    by_id = {entry["charInstId"]: entry for entry in journal["squad"]}
    # The client resolves alternate templates through PlayerCharacter.GetSkills;
    # seeded accounts have no ``tmpl`` patches, so the replay must request the
    # default template (empty tmplId) or CheckIfCharSkillAvailable NREs.
    assert all(entry["tmplId"] == "" for entry in journal["squad"])
    assert all(log["signiture"]["uniqueId"] in by_id for log in journal["logs"])
    assert [log["timestamp"] for log in journal["logs"]] == sorted(
        log["timestamp"] for log in journal["logs"]
    )
    assert by_id[264]["level"] == 90
    assert by_id[4081]["level"] == 80
    assert by_id[180]["level"] == 90
    assert by_id[204]["level"] == 80
