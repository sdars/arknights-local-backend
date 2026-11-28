import json
import sqlite3
import sys
import time
from pathlib import Path

from conftest import DB_PATH

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import game_data  # noqa: E402


SHOP_ENDPOINTS = [
    "/shop/getCashGoodList",
    "/shop/getLowGoodList",
    "/shop/getHighGoodList",
    "/shop/getExtraGoodList",
    "/shop/getGPGoodList",
    "/shop/getLMTGSGoodList",
    "/shop/getEPGSGoodList",
    "/shop/getRepGoodList",
    "/shop/getSocialGoodList",
    "/shop/getClassicGoodList",
    "/shop/getSkinGoodList",
]


def test_sync_status_returns_result_object(client, login):
    response = client.post(
        f"{client.base_url}/account/syncStatus",
        headers=login["headers"],
        json={"modules": 0, "params": {}},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body["result"], dict)
    assert isinstance(body["ts"], int)
    assert body["playerDataDelta"]["deleted"] == {}
    # An unchanged poll must be empty; the client treats a non-empty delta as
    # a reason to show its data-resync prompt.
    assert body["playerDataDelta"]["modified"] == {}


def test_cash_good_list_has_goods(client, login):
    response = client.post(
        f"{client.base_url}/shop/getCashGoodList",
        headers=login["headers"],
        json={},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["goodList"]) == 6
    assert body["goodList"][0]["goodId"] == "CS_1_r7"
    assert body["playerDataDelta"] == {"deleted": {}, "modified": {}}


def test_skin_goods_normalize_expired_countdown(client, login):
    body = client.post(
        f"{client.base_url}/shop/getSkinGoodList",
        headers=login["headers"],
        json={},
        timeout=15,
    ).json()
    now = int(time.time())
    goods = body["goodList"]
    assert len(goods) == 165
    assert not [
        row
        for row in goods
        if row.get("endDateTime") is not None and int(row["endDateTime"]) <= now
    ]
    assert any(row.get("endDateTime") is None for row in goods)
    assert any(
        row.get("endDateTime") is not None and int(row["endDateTime"]) > now
        for row in goods
    )
    skins = (game_data.excel("skin_table.json") or {}).get("charSkins") or {}
    for row in goods:
        display = (skins.get(row["skinId"]) or {}).get("displaySkin") or {}
        assert "采购中心" in str(display.get("obtainApproach") or "")


def test_shop_lists_are_populated(client, login):
    for path in SHOP_ENDPOINTS:
        response = client.post(
            f"{client.base_url}{path}",
            headers=login["headers"],
            json={},
            timeout=20,
        )
        assert response.status_code == 200, (path, response.text)
        body = response.json()
        assert "playerDataDelta" in body, path
        assert any(key in body for key in ("goodList", "goods", "groups", "weeklyGroup")), path


def test_gp_good_list_exposes_item_list_for_detail_panel(client, login):
    response = client.post(
        f"{client.base_url}/shop/getGPGoodList",
        headers=login["headers"],
        json={},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    packages = []
    for key in ("weeklyGroup", "monthlyGroup"):
        packages.extend((body.get(key) or {}).get("packages", {}).values())
    for key in ("monthlySub", "levelGP", "oneTimeGP"):
        packages.extend(body.get(key) or [])
    assert packages
    for package in packages:
        assert isinstance(package.get("items"), list)
        assert package.get("itemList") == package["items"]


def test_buy_free_level_pack_and_purchase_state(client, login):
    response = client.post(
        f"{client.base_url}/shop/buyGPGood",
        headers=login["headers"],
        json={"goodId": "GP_freeL_5", "enterTs": 0},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == 0
    assert any(item["id"] == "4001" and int(item["count"]) >= 2000 for item in body["items"])
    state = client.post(
        f"{client.base_url}/shop/getGoodPurchaseState",
        headers=login["headers"],
        json={"goodIdMap": {"GP": ["GP_freeL_5"]}},
        timeout=15,
    ).json()
    # getGoodPurchaseState returns remaining stock, so a limit-1 item that was
    # just bought is omitted (the client treats a missing key as sold out).
    assert state["result"].get("GP_freeL_5", 0) == 0
    duplicate = client.post(
        f"{client.base_url}/shop/buyGPGood",
        headers=login["headers"],
        json={"goodId": "GP_freeL_5", "enterTs": 0},
        timeout=15,
    ).json()
    assert duplicate["result"] == 1
    assert duplicate["error"] == "purchase limit reached"


def test_buy_gp_good_with_ticket(client, login, test_account):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT user_json FROM accounts WHERE uid=?", (int(test_account["uid"]),)
        ).fetchone()
        user = json.loads(row[0])
        user.setdefault("inventory", {})["giftPackageTicket_3_2"] = 1
        conn.execute(
            "UPDATE accounts SET user_json=? WHERE uid=?",
            (json.dumps(user, ensure_ascii=False, separators=(",", ":")), int(test_account["uid"])),
        )
    response = client.post(
        f"{client.base_url}/shop/buyGPGoodWithTicket",
        headers=login["headers"],
        json={"goodId": "GP_gM_96_M_1", "ticketId": "giftPackageTicket_3_2"},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == 0
    assert any(item["id"] == "7004" for item in body["items"])
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT user_json FROM accounts WHERE uid=?", (int(test_account["uid"]),)
        ).fetchone()
    assert "giftPackageTicket_3_2" not in (json.loads(row[0]).get("inventory") or {})
