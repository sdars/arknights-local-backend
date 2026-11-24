import json
import sqlite3
import sys
from pathlib import Path

from conftest import DB_PATH

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import game_data  # noqa: E402


def _load_user(uid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()
    return json.loads(row[0])


def _store_user(uid, user):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE accounts SET user_json=? WHERE uid=?",
            (json.dumps(user, ensure_ascii=False, separators=(",", ":")), int(uid)),
        )


def test_change_char_skin_equips_default_and_rejects_unowned(client, login, test_account):
    base = client.base_url
    user = client.post(f"{base}/account/syncData", headers=login["headers"], json={}, timeout=15).json()["user"]
    chars = user["troop"]["chars"]
    inst = next(key for key, value in chars.items() if isinstance(value, dict) and value.get("charId"))
    char_id = chars[inst]["charId"]

    response = client.post(
        f"{base}/charBuild/changeCharSkin",
        headers=login["headers"],
        json={"charInstId": int(inst), "skinId": f"{char_id}#1"},
        timeout=15,
    )
    body = response.json()
    assert response.status_code == 200, response.text
    assert body["result"] == 0
    assert body["playerDataDelta"]["modified"]["troop"]["chars"][str(inst)]["skin"] == f"{char_id}#1"

    table = (game_data.excel("skin_table.json") or {}).get("charSkins") or {}
    owned = (user.get("skin") or {}).get("characterSkins") or {}
    unowned = next(
        skin_id
        for skin_id, meta in table.items()
        if meta.get("charId") == char_id and meta.get("isBuySkin") and skin_id not in owned
    )
    rejected = client.post(
        f"{base}/charBuild/changeCharSkin",
        headers=login["headers"],
        json={"charInstId": int(inst), "skinId": unowned},
        timeout=15,
    ).json()
    assert rejected["result"] == 1
    assert rejected["playerDataDelta"]["modified"] == {}
    assert _load_user(test_account["uid"])["troop"]["chars"][str(inst)]["skin"] == f"{char_id}#1"


def test_sync_repairs_illegal_equipped_skin(client, login, test_account):
    base = client.base_url
    user = client.post(f"{base}/account/syncData", headers=login["headers"], json={}, timeout=15).json()["user"]
    chars = user["troop"]["chars"]
    inst = next(key for key, value in chars.items() if isinstance(value, dict) and value.get("charId"))
    char_id = chars[inst]["charId"]

    table = (game_data.excel("skin_table.json") or {}).get("charSkins") or {}
    owned = (user.get("skin") or {}).get("characterSkins") or {}
    illegal = next(
        skin_id
        for skin_id, meta in table.items()
        if meta.get("charId") == char_id and meta.get("isBuySkin") and skin_id not in owned
    )
    stored = _load_user(test_account["uid"])
    stored["troop"]["chars"][str(inst)]["skin"] = illegal
    _store_user(test_account["uid"], stored)

    synced = client.post(f"{base}/account/syncData", headers=login["headers"], json={}, timeout=15).json()
    assert synced["result"] == 0
    assert synced["user"]["troop"]["chars"][str(inst)]["skin"] == f"{char_id}#1"


def test_change_skin_sp_state_persists_flag(client, login, test_account):
    base = client.base_url
    skin_id = "char_002_amiya#2"
    body = client.post(
        f"{base}/charBuild/changeSkinSpState",
        headers=login["headers"],
        json={"skinId": skin_id, "isSpecial": True},
        timeout=15,
    ).json()
    assert body["result"] == 0
    assert body["playerDataDelta"]["modified"]["skin"]["skinSp"][skin_id] is True
    assert _load_user(test_account["uid"])["skin"]["skinSp"][skin_id] is True


def test_change_mark_star_persists(client, login, test_account):
    base = client.base_url
    user = client.post(f"{base}/account/syncData", headers=login["headers"], json={}, timeout=15).json()["user"]
    chars = user["troop"]["chars"]
    inst = next(key for key, value in chars.items() if isinstance(value, dict) and value.get("charId"))
    body = client.post(
        f"{base}/char/changeMarkStar",
        headers=login["headers"],
        json={"set": {str(inst): 1}},
        timeout=15,
    ).json()
    assert body["result"] == 0
    assert body["playerDataDelta"]["modified"]["troop"]["chars"][str(inst)]["starMark"] == 1
    assert _load_user(test_account["uid"])["troop"]["chars"][str(inst)]["starMark"] == 1


def test_change_char_skin_accepts_owned_skin(client, login, test_account):
    base = client.base_url
    user = client.post(f"{base}/account/syncData", headers=login["headers"], json={}, timeout=15).json()["user"]
    chars = user["troop"]["chars"]
    inst = next(key for key, value in chars.items() if isinstance(value, dict) and value.get("charId"))
    char_id = chars[inst]["charId"]
    table = (game_data.excel("skin_table.json") or {}).get("charSkins") or {}
    skin_id = next(
        key
        for key, meta in table.items()
        if meta.get("charId") == char_id and meta.get("isBuySkin")
    )
    stored = _load_user(test_account["uid"])
    stored["skin"]["characterSkins"][skin_id] = 1
    _store_user(test_account["uid"], stored)

    body = client.post(
        f"{base}/charBuild/changeCharSkin",
        headers=login["headers"],
        json={"charInstId": int(inst), "skinId": skin_id},
        timeout=15,
    ).json()
    assert body["result"] == 0
    assert body["playerDataDelta"]["modified"]["troop"]["chars"][str(inst)]["skin"] == skin_id
    assert _load_user(test_account["uid"])["troop"]["chars"][str(inst)]["skin"] == skin_id
