import copy
import json
import os
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import gacha_service, grants  # noqa: E402


DB_PATH = Path(os.environ.get("ARK_DB", ROOT / "backend" / "data" / "arknights.sqlite3"))


def _template():
    return json.loads((ROOT / "backend" / "data" / "player_template.json").read_text(encoding="utf-8"))


def _admin_login(client):
    response = client.post(
        f"{client.base_url}/admin/login",
        data={
            "username": os.environ.get("ARK_ADMIN_USER", "sdars"),
            "password": os.environ.get("ARK_ADMIN_PASSWORD", "123456"),
        },
        allow_redirects=False,
        timeout=10,
    )
    assert response.status_code == 303


def _sync_user(client, login):
    body = client.post(
        f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=20
    ).json()
    assert body["result"] == 0, body
    return body["user"]


def test_newbee_pool_uses_discounted_price():
    meta = gacha_service.pool_meta("BOOT_0_1_3")
    assert meta["singlePrice"] == 380
    assert meta["tenPrice"] == 3800
    assert gacha_service.pool_meta("NORM_79_0_4")["tenPrice"] == 6000


def test_newbee_ten_pull_charges_3800_and_updates_remaining_count():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 3800
    user["gacha"]["newbee"] = {"cnt": 21, "poolId": "BOOT_0_1_3", "openFlag": 1}
    result = gacha_service.draw(user, "BOOT_0_1_3", count=10, use_tkt=0)
    assert result["result"] == 0, result
    assert user["status"]["diamondShard"] == 0
    assert user["gacha"]["newbee"]["cnt"] == 11
    assert len(result["gachaResultList"]) == 10


def test_newbee_pool_rejects_draw_beyond_remaining_count():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 3800
    user["gacha"]["newbee"] = {"cnt": 5, "poolId": "BOOT_0_1_3", "openFlag": 1}
    result = gacha_service.draw(user, "BOOT_0_1_3", count=10, use_tkt=0)
    assert result["result"] == 400
    assert user["status"]["diamondShard"] == 3800
    assert user["gacha"]["newbee"]["cnt"] == 5


def test_ten_pull_permit_is_consumed_without_diamond_shard():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 0
    user["status"]["tenGachaTicket"] = 1
    result = gacha_service.draw(user, "NORM_79_0_4", count=10, use_tkt=2)
    assert result["result"] == 0, result
    assert user["status"]["tenGachaTicket"] == 0
    assert user["status"]["diamondShard"] == 0


def test_exchange_diamond_shard_uses_account_platform(client, login, test_account):
    _admin_login(client)
    uid = int(test_account["uid"])
    response = client.post(
        f"{client.base_url}/admin/accounts/{uid}/status",
        data={"androidDiamond": "100", "iosDiamond": "50", "diamondShard": "0"},
        allow_redirects=False,
        timeout=15,
    )
    assert response.status_code == 303
    exchanged = client.post(
        f"{client.base_url}/user/exchangeDiamondShard",
        headers=login["headers"],
        json={"count": 10},
        timeout=15,
    ).json()
    assert exchanged["result"] == 0, exchanged
    user = _sync_user(client, login)
    assert user["status"]["androidDiamond"] == 90
    assert user["status"]["iosDiamond"] == 50
    assert user["status"]["diamondShard"] == 1800

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE accounts SET platform='ios' WHERE uid=?", (uid,))
    try:
        exchanged = client.post(
            f"{client.base_url}/user/exchangeDiamondShard",
            headers=login["headers"],
            json={"count": 5},
            timeout=15,
        ).json()
        assert exchanged["result"] == 0, exchanged
        user = _sync_user(client, login)
        assert user["status"]["androidDiamond"] == 90
        assert user["status"]["iosDiamond"] == 45
        assert user["status"]["diamondShard"] == 2700
    finally:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE accounts SET platform='android' WHERE uid=?", (uid,))

    too_many = client.post(
        f"{client.base_url}/user/exchangeDiamondShard",
        headers=login["headers"],
        json={"count": 10000},
        timeout=15,
    ).json()
    assert too_many["result"] == 1
    assert _sync_user(client, login)["status"]["diamondShard"] == 2700


def test_diamond_status_key_prefers_explicit_platform():
    assert grants.diamond_status_key({"localPlatform": "ios"}) == "iosDiamond"
    assert grants.diamond_status_key({"localPlatform": "android"}, "ios") == "iosDiamond"
