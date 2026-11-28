import json
import random
import sqlite3
import sys
from pathlib import Path

from conftest import DB_PATH

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import create_account  # noqa: E402


def _load_user(uid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()
    assert row, uid
    return json.loads(row[0])


def _sync(client, login):
    response = client.post(
        f"{client.base_url}/account/syncData",
        headers=login["headers"],
        json={},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]


def test_lock_char_persists_and_blocks_sale(client, login, test_account):
    base = client.base_url
    user = _sync(client, login)
    inst_id = str(next(iter(user["troop"]["chars"])))

    locked = client.post(
        f"{base}/charBuild/lockChar",
        headers=login["headers"],
        json={"charInstIdList": [int(inst_id)], "locked": 1},
        timeout=15,
    ).json()
    assert locked["result"] == 0
    assert locked["playerDataDelta"]["modified"]["troop"]["chars"][inst_id]["locked"] == 1
    assert _load_user(test_account["uid"])["troop"]["chars"][inst_id]["locked"] == 1

    rejected = client.post(
        f"{base}/charBuild/sellChar",
        headers=login["headers"],
        json={"charInstIdList": [int(inst_id)]},
        timeout=15,
    ).json()
    assert rejected["result"] == 1
    assert "locked" in rejected["error"]
    assert inst_id in _load_user(test_account["uid"])["troop"]["chars"]

    unlocked = client.post(
        f"{base}/charBuild/lockChar",
        headers=login["headers"],
        json={"charInstIdList": [int(inst_id)], "locked": 0},
        timeout=15,
    ).json()
    assert unlocked["result"] == 0
    assert _load_user(test_account["uid"])["troop"]["chars"][inst_id]["locked"] == 0


def test_sell_char_removes_roster_references(client):
    phone = "187" + f"{random.SystemRandom().randrange(10**8):08d}"
    account, _created = create_account(phone, nickname="出售测试")
    uid = str(account["uid"])
    headers = {"secret": account["token"], "uid": uid}
    try:
        user = _load_user(uid)
        chars = user["troop"]["chars"]
        inst_id = str(next(iter(chars)))
        char_id = chars[inst_id]["charId"]
        before = int((user.get("status") or {}).get("gold") or 0)
        response = client.post(
            f"{client.base_url}/charBuild/sellChar",
            headers=headers,
            json={"charInstIdList": [int(inst_id)]},
            timeout=15,
        )
        body = response.json()
        assert response.status_code == 200, response.text
        assert body["result"] == 0, body
        assert body["items"]
        assert body["playerDataDelta"]["deleted"]["troop"]["chars"] == [int(inst_id)]
        stored = _load_user(uid)
        assert inst_id not in stored["troop"]["chars"]
        assert inst_id not in (stored.get("building") or {}).get("chars", {})
        assert int(stored["status"]["gold"]) > before
        if not any(value.get("charId") == char_id for value in stored["troop"]["chars"].values()):
            assert char_id not in (stored["troop"].get("charGroup") or {})
    finally:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM sessions WHERE account_id=?", (account["id"],))
            conn.execute("DELETE FROM accounts WHERE id=?", (account["id"],))


def test_gallery_thumbnail_is_a_generated_png(client, login):
    base = client.base_url
    magazine_id = "diy_protocol_test_thumbnail"
    saved = client.post(
        f"{base}/gallery/saveDiyMagazineV1",
        headers=login["headers"],
        json={
            "magazine": {
                "id": magazine_id,
                "name": "协议测试杂志",
                "slots": [{"charId": "char_002_amiya"}],
            }
        },
        timeout=15,
    ).json()
    assert saved["result"] == 0

    payload = client.post(
        f"{base}/gallery/getThumbnailUrl",
        headers=login["headers"],
        json={"idList": [magazine_id]},
        timeout=15,
    ).json()
    assert payload["result"] == 0
    url = payload["url"][magazine_id]
    image = client.get(f"{base}{url}", timeout=15)
    assert image.status_code == 200, image.text
    assert image.headers["content-type"].startswith("image/png")
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(image.content) > 1000


def test_non_game_health_and_probe_routes_are_served(client):
    assert client.get(f"{client.base_url}/version", timeout=10).status_code == 200
    assert client.get(f"{client.base_url}/favicon.ico", timeout=10).status_code == 200
    assert client.head(f"{client.base_url}/", timeout=10).status_code == 200
    assert client.options(f"{client.base_url}/", timeout=10).status_code == 204
