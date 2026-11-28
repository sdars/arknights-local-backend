import json
import sqlite3

from conftest import DB_PATH


def _load_user(uid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()
    return json.loads(row[0])


def _sync(client, login):
    return client.post(f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=15).json()["user"]


def test_update_preset_slots_and_secretary(client, login, test_account):
    base = client.base_url
    user = _sync(client, login)
    rotation = user["charRotation"]
    inst_id = str(rotation["current"])
    chars = [value["charId"] for value in user["troop"]["chars"].values() if isinstance(value, dict) and value.get("charId")]
    slots = [
        {"charId": chars[0], "skinId": f"{chars[0]}#1", "skinSp": False},
        {"charId": chars[1], "skinId": f"{chars[1]}#1", "skinSp": True},
    ]
    body = client.post(
        f"{base}/charRotation/updatePreset",
        headers=login["headers"],
        json={"instId": inst_id, "flag": 24, "data": {"profile": f"{chars[0]}#1", "profileInst": "1", "profileSp": False, "slots": slots}},
        timeout=15,
    ).json()
    assert body["result"] == 0
    preset = body["playerDataDelta"]["modified"]["charRotation"]["preset"][inst_id]
    assert preset["slots"] == slots
    assert preset["profile"] == f"{chars[0]}#1"

    stored = _load_user(test_account["uid"])["charRotation"]["preset"][inst_id]
    assert stored["slots"] == slots
    assert stored["profile"] == f"{chars[0]}#1"


def test_update_preset_name_rejects_placeholder_question_marks(client, login, test_account):
    base = client.base_url
    user = _sync(client, login)
    inst_id = str(user["charRotation"]["current"])
    body = client.post(
        f"{base}/charRotation/updatePreset",
        headers=login["headers"],
        json={"instId": inst_id, "flag": 1, "data": {"name": "???????"}},
        timeout=15,
    ).json()
    assert body["result"] == 0
    assert body["playerDataDelta"]["modified"]["charRotation"]["preset"][inst_id]["name"] != "???????"
    stored = _load_user(test_account["uid"])["charRotation"]["preset"][inst_id]
    assert stored["name"] != "???????"


def test_update_preset_background_and_theme(client, login, test_account):
    base = client.base_url
    user = _sync(client, login)
    inst_id = str(user["charRotation"]["current"])
    bg = client.post(
        f"{base}/charRotation/updatePreset",
        headers=login["headers"],
        json={"instId": inst_id, "flag": 2, "data": {"background": "bg_rhodes_night"}},
        timeout=15,
    ).json()
    assert bg["result"] == 0
    assert bg["playerDataDelta"]["modified"]["charRotation"]["preset"][inst_id]["background"] == "bg_rhodes_night"

    theme = client.post(
        f"{base}/charRotation/updatePreset",
        headers=login["headers"],
        json={"instId": inst_id, "flag": 4, "data": {"homeTheme": "tm_rhodes_night"}},
        timeout=15,
    ).json()
    assert theme["result"] == 0
    preset = _load_user(test_account["uid"])["charRotation"]["preset"][inst_id]
    assert preset["background"] == "bg_rhodes_night"
    assert preset["homeTheme"] == "tm_rhodes_night"


def test_create_set_current_and_delete_preset(client, login, test_account):
    base = client.base_url
    user = _sync(client, login)
    original = str(user["charRotation"]["current"])
    created = client.post(f"{base}/charRotation/createPreset", headers=login["headers"], json={}, timeout=15).json()
    assert created["result"] == 0
    new_id = str(created["instId"])
    assert new_id in created["playerDataDelta"]["modified"]["charRotation"]["preset"]

    current = client.post(
        f"{base}/charRotation/setCurrent", headers=login["headers"], json={"instId": new_id}, timeout=15
    ).json()
    assert current["result"] == 0
    assert current["playerDataDelta"]["modified"]["charRotation"]["current"] == new_id
    assert _load_user(test_account["uid"])["charRotation"]["current"] == new_id

    deleted = client.post(
        f"{base}/charRotation/deletePreset", headers=login["headers"], json={"instId": new_id}, timeout=15
    ).json()
    assert deleted["result"] == 0
    stored = _load_user(test_account["uid"])["charRotation"]
    assert new_id not in stored["preset"]
    assert stored["current"] != new_id


def test_sync_status_exposes_char_rotation(client, login):
    # The periodic sync is incremental.  Change the stored rotation directly
    # (the same path the admin console uses) and verify it is surfaced.
    uid = int(login["headers"]["uid"])
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (uid,)).fetchone()
        user = json.loads(row[0])
        presets = user.setdefault("charRotation", {}).setdefault("preset", {})
        presets["sync_probe"] = {"name": "sync probe"}
        conn.execute(
            "UPDATE accounts SET user_json=? WHERE uid=?",
            (json.dumps(user, ensure_ascii=False, separators=(",", ":")), uid),
        )
    body = client.post(
        f"{client.base_url}/account/syncStatus",
        headers=login["headers"],
        json={"modules": 0, "params": {}},
        timeout=15,
    ).json()
    assert body["playerDataDelta"]["modified"]["charRotation"]["preset"]["sync_probe"] == {"name": "sync probe"}
