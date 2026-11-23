import json
import os
import random
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("ARK_DB", ROOT / "backend" / "data" / "arknights.sqlite3"))


def admin_login(client):
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


def load_user(uid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()
    assert row
    return json.loads(row[0])


def save_user(uid, user):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE accounts SET user_json=? WHERE uid=?",
            (json.dumps(user, ensure_ascii=False, separators=(",", ":")), int(uid)),
        )


def test_product_admin_add_edit_price_and_delete(client, login, test_account):
    admin_login(client)
    product_id = f"GP_Test_{random.SystemRandom().randrange(100000, 999999)}"
    created = client.post(
        f"{client.base_url}/admin/products/create",
        data={
            "product_id": product_id,
            "appstore_id": product_id,
            "name": "协议测试商品",
            "price": "1234",
            "platform": "2",
            "status": "1",
        },
        allow_redirects=False,
        timeout=15,
    )
    assert created.status_code == 303
    body = client.post(f"{client.base_url}/u8/pay/getAllProductList", json={}, timeout=15).json()
    row = next((item for item in body["productList"] if item.get("product_id") == product_id), None)
    assert row and row["price"] == 1234 and row["status"] == 1
    store_id = row["store_id"]

    updated = client.post(
        f"{client.base_url}/admin/products/{product_id}/update",
        data={
            "product_id": product_id,
            "appstore_id": product_id,
            "store_id": str(store_id),
            "name": "协议测试商品-改价",
            "price": "2000",
            "platform": "2",
            "status": "1",
        },
        allow_redirects=False,
        timeout=15,
    )
    assert updated.status_code == 303
    body = client.post(f"{client.base_url}/u8/pay/getAllProductList", json={}, timeout=15).json()
    row = next(item for item in body["productList"] if item.get("product_id") == product_id)
    assert row["price"] == 2000
    assert row["name"] == "协议测试商品-改价"
    page = client.get(f"{client.base_url}/admin/products/{product_id}", timeout=15)
    assert page.status_code == 200
    assert "发放内容" in page.text

    content = client.post(
        f"{client.base_url}/admin/products/{product_id}/content",
        data={"item_id": "4002", "item_type": "DIAMOND", "item_count": "123"},
        allow_redirects=False,
        timeout=15,
    )
    assert content.status_code == 303
    before = load_user(test_account["uid"]).get("status", {}).get("androidDiamond", 0)
    order = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": store_id, "goodId": product_id, "platform": "android"},
        timeout=15,
    ).json()
    assert order["result"] == 0, order
    confirmed = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": order["orderId"]},
        timeout=15,
    ).json()
    assert confirmed["result"] == 0, confirmed
    after = load_user(test_account["uid"]).get("status", {}).get("androidDiamond", 0)
    assert after == before + 123

    deleted = client.post(
        f"{client.base_url}/admin/products/{product_id}/delete",
        allow_redirects=False,
        timeout=15,
    )
    assert deleted.status_code == 303
    body = client.post(f"{client.base_url}/u8/pay/getAllProductList", json={}, timeout=15).json()
    assert not any(item.get("product_id") == product_id for item in body["productList"])


def test_gacha_admin_can_create_modify_rates_and_draw(client, login, test_account):
    admin_login(client)
    pool_id = f"NORM_PROBE_{random.SystemRandom().randrange(100000, 999999)}"
    try:
        created = client.post(
            f"{client.base_url}/admin/gacha/create",
            data={
                "pool_id": pool_id,
                "name": "协议测试寻访",
                "base_pool": "NORM_79_0_3",
                "single_price": "600",
                "ten_price": "6000",
                "up_chars": "char_103_angel",
                "up_rate_percent": "100",
                "rate6_percent": "100",
                "rate5_percent": "0",
                "rate4_percent": "0",
                "rate3_percent": "0",
                "guarantee5": "0",
                "guarantee5_count": "10",
            },
            allow_redirects=False,
            timeout=20,
        )
        assert created.status_code == 303
        detail = client.post(
            f"{client.base_url}/gacha/getPoolDetail", json={"poolId": pool_id}, timeout=20
        ).json()
        per = detail["detailInfo"]["availCharInfo"]["perAvailList"]
        assert next(row for row in per if row["rarityRank"] == 5)["totalPercent"] == 1.0

        user = load_user(test_account["uid"])
        user["status"]["diamondShard"] = 60000
        save_user(test_account["uid"], user)
        drawn = client.post(
            f"{client.base_url}/gacha/tenAdvancedGacha",
            headers=login["headers"],
            json={"poolId": pool_id, "useTkt": 0, "itemList": []},
            timeout=30,
        ).json()
        assert drawn["result"] == 0, drawn
        assert len(drawn["gachaResultList"]) == 10
        assert all(row["rarity"] == 5 for row in drawn["gachaResultList"])
        assert all(row["charId"] == "char_103_angel" for row in drawn["gachaResultList"])
        synced = client.post(
            f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=20
        ).json()
        assert synced["user"]["status"]["diamondShard"] == 54000
        assert any(char.get("charId") == "char_103_angel" for char in synced["user"]["troop"]["chars"].values())
    finally:
        client.post(f"{client.base_url}/admin/gacha/{pool_id}/delete", allow_redirects=False, timeout=15)


def test_operator_admin_values_and_char_build_are_visible_in_sync(client, login, test_account):
    admin_login(client)
    uid = test_account["uid"]
    changed = client.post(
        f"{client.base_url}/admin/accounts/{uid}/operator/2",
        data={
            "level": "80",
            "evolvePhase": "2",
            "potentialRank": "3",
            "mainSkillLvl": "7",
            "favorPoint": "200",
            "skin": "char_002_amiya#1",
            "voiceLan": "CN_MANDARIN",
        },
        allow_redirects=False,
        timeout=15,
    )
    assert changed.status_code == 303
    online = client.post(
        f"{client.base_url}/account/syncStatus",
        headers=login["headers"],
        json={"modules": 0, "params": {}},
        timeout=20,
    ).json()
    amiya = online["playerDataDelta"]["modified"]["troop"]["chars"]["2"]
    assert amiya["level"] == 80 and amiya["evolvePhase"] == 2

    user = load_user(uid)
    user["troop"]["chars"]["2"].update({"level": 1, "exp": 0, "evolvePhase": 0})
    user.setdefault("inventory", {})["2004"] = 10
    user["status"]["gold"] = 500000
    save_user(uid, user)
    upgraded = client.post(
        f"{client.base_url}/charBuild/upgradeChar",
        headers=login["headers"],
        json={"charInstId": 2, "expMats": [{"id": "2004", "count": 10}]},
        timeout=20,
    ).json()
    assert upgraded["result"] == 0, upgraded
    level = upgraded["playerDataDelta"]["modified"]["troop"]["chars"]["2"]["level"]
    assert level > 1
    synced = client.post(
        f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=20
    ).json()
    assert synced["user"]["troop"]["chars"]["2"]["level"] == level


def test_shop_purchase_limit_returns_remaining_and_persists(client, login, test_account):
    uid = test_account["uid"]
    user = load_user(uid)
    user["status"]["lggShard"] = 1000
    user.get("shop", {}).setdefault("LS", {}).setdefault("info", [])
    save_user(uid, user)
    good_id = "LS_lggShdShopnumber91_1"

    state = client.post(
        f"{client.base_url}/account/syncStatus",
        headers=login["headers"],
        json={"modules": 16, "params": {"16": {"goodIdMap": {"LS": [good_id]}}}},
        timeout=20,
    ).json()
    assert state["result"]["16"]["goodPurchaseState"]["result"][good_id] == 2

    bought = client.post(
        f"{client.base_url}/shop/buyLowGood",
        headers=login["headers"],
        json={"goodId": good_id, "count": 1},
        timeout=20,
    ).json()
    assert bought["result"] == 0, bought
    state = client.post(
        f"{client.base_url}/account/syncStatus",
        headers=login["headers"],
        json={"modules": 16, "params": {"16": {"goodIdMap": {"LS": [good_id]}}}},
        timeout=20,
    ).json()
    assert state["result"]["16"]["goodPurchaseState"]["result"][good_id] == 1

    second = client.post(
        f"{client.base_url}/shop/buyLowGood",
        headers=login["headers"],
        json={"goodId": good_id, "count": 1},
        timeout=20,
    ).json()
    assert second["result"] == 0
    state = client.post(
        f"{client.base_url}/account/syncStatus",
        headers=login["headers"],
        json={"modules": 16, "params": {"16": {"goodIdMap": {"LS": [good_id]}}}},
        timeout=20,
    ).json()
    assert good_id not in state["result"]["16"]["goodPurchaseState"]["result"]
    third = client.post(
        f"{client.base_url}/shop/buyLowGood",
        headers=login["headers"],
        json={"goodId": good_id, "count": 1},
        timeout=20,
    ).json()
    assert third["result"] == 1


def test_admin_mainline_sync_marks_early_stages_passed(client, login, test_account):
    uid = test_account["uid"]
    user = load_user(uid)
    user.setdefault("status", {})["mainStageProgress"] = ""
    stages = user.setdefault("dungeon", {}).setdefault("stages", {})
    stages.setdefault("main_00-01", {})
    stages["main_00-01"].update({"state": 0, "completeTimes": 0, "startTimes": 0})
    save_user(uid, user)
    admin_login(client)
    response = client.post(
        f"{client.base_url}/admin/accounts/{uid}/status",
        data={"mainStageProgress": "0-10"},
        allow_redirects=False,
        timeout=20,
    )
    assert response.status_code == 303
    synced = client.post(
        f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=20
    ).json()["user"]
    assert synced["status"]["mainStageProgress"] == "main_00-10"
    assert synced["dungeon"]["stages"]["main_00-01"]["state"] == 3
    assert synced["dungeon"]["stages"]["main_00-01"]["completeTimes"] >= 1
    assert synced["dungeon"]["stages"]["main_00-10"]["state"] == 3
    assert synced["status"]["progress"] >= 1100
    assert synced["status"]["flags"]["obt/guide/l0-1/0_mission_main"] == 1
    assert synced["status"]["flags"]["obt/guide/l0-1/1_training_level"] == 1

