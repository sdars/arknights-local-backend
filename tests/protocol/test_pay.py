import json
import sqlite3
from urllib.parse import unquote

from conftest import DB_PATH


def test_create_order_returns_sdk_extension(client, login):
    response = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 5399, "goodId": "GP_Once_385"},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == 0
    assert body["orderId"]
    extension = json.loads(body["extension"])
    assert extension["amount"] == 4500
    assert extension["outOrderId"] == body["orderId"]
    assert extension["extension"]["appStoreProductId"] == "GP_Once_385"


def test_sdk_app_product_and_state(client, login, test_account):
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 5399, "goodId": "GP_Once_385"},
        timeout=15,
    ).json()
    sdk = client.post(
        f"{client.base_url}/user/pay/order/v2/create/app_product",
        json={
            "token": test_account["token"],
            "appCode": "7318def77669979d",
            "signParams": "null",
            "platform": 1,
            "source": 0,
        },
        timeout=15,
    )
    assert sdk.status_code == 200, sdk.text
    sdk_body = sdk.json()
    assert sdk_body["status"] == 0
    assert sdk_body["data"]["orderId"] == created["orderId"]
    assert len(sdk_body["data"]["payment"]) == 2
    state = client.get(
        f"{client.base_url}/pay/order/v1/state",
        params={"orderId": created["orderId"]},
        timeout=15,
    ).json()
    assert state["status"] == 101
    assert state["data"]["orderId"] == created["orderId"]


def test_confirm_order_marks_paid(client, login):
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 6818, "goodId": "GP_Once_411"},
        timeout=15,
    ).json()
    confirmed = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": created["orderId"]},
        timeout=15,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["result"] == 0
    state = client.get(
        f"{client.base_url}/pay/order/v1/state",
        params={"orderId": created["orderId"]},
        timeout=15,
    ).json()
    assert state["status"] == 0


def test_hgsdk_alipay_page_pay_and_success_callback(client, login, test_account):
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 5399, "goodId": "GP_Once_385"},
        timeout=15,
    ).json()
    order_id = created["orderId"]
    bill = client.post(
        f"{client.base_url}/pay/createOrderAlipay",
        json={
            "token": test_account["token"],
            "orderId": order_id,
            "extra": {"alipayAppId": "local", "alipayMethod": 2},
        },
        timeout=15,
    )
    assert bill.status_code == 200, bill.text
    body = bill.json()
    assert body["pagePay"] is True
    assert f"out_trade_no={order_id}" in body["qs"]
    assert "/pay/success" in unquote(body["qs"])

    success = client.get(
        f"{client.base_url}/pay/success", params={"orderId": order_id}, timeout=15
    )
    assert success.status_code == 200, success.text
    assert "支付成功" in success.text
    state = client.get(
        f"{client.base_url}/pay/order/v1/state",
        params={"orderId": order_id},
        timeout=15,
    ).json()
    assert state["status"] == 0


def test_hgsdk_alipay_confirm_marks_paid(client, login, test_account):
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 5398, "goodId": "GP_Once_384"},
        timeout=15,
    ).json()
    confirmed = client.post(
        f"{client.base_url}/pay/confirmOrderAlipay",
        json={
            "token": test_account["token"],
            "orderId": created["orderId"],
            "result": "local-confirmed",
            "status": 0,
        },
        timeout=15,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == 0
    state = client.get(
        f"{client.base_url}/pay/order/v1/state",
        params={"orderId": created["orderId"]},
        timeout=15,
    ).json()
    assert state["status"] == 0


def test_hgsdk_wechat_bill_shape(client, login, test_account):
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 5399, "goodId": "GP_Once_385"},
        timeout=15,
    ).json()
    bill = client.post(
        f"{client.base_url}/pay/createOrderWechat",
        json={"token": test_account["token"], "orderId": created["orderId"]},
        timeout=15,
    )
    assert bill.status_code == 200, bill.text
    body = bill.json()
    assert body["status"] == 0
    assert body["requestObj"]["prepayid"] == created["orderId"]


def test_check_order_v2_returns_sdk_status(client, login):
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 6818, "goodId": "GP_Once_411"},
        timeout=15,
    ).json()
    checked = client.post(
        f"{client.base_url}/pay/order/v2/check/alipay",
        json={"orderId": created["orderId"]},
        timeout=15,
    )
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["status"] == 0
    assert body["data"]["orderId"] == created["orderId"]


def test_u8_confirm_order_state_reports_succeed(client, login, test_account):
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 6818, "goodId": "GP_Once_411"},
        timeout=15,
    ).json()
    client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": created["orderId"]},
        timeout=15,
    )
    state = client.post(
        f"{client.base_url}/u8/pay/confirmOrderState",
        json={"token": test_account["token"], "orderId": created["orderId"]},
        timeout=15,
    )
    assert state.status_code == 200, state.text
    body = state.json()
    assert body["status"] == 4
    assert body["payState"] == 3


def test_confirm_order_returns_shop_good_items(client, login):
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 6818, "goodId": "GP_Once_411"},
        timeout=15,
    ).json()
    confirmed = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": created["orderId"]},
        timeout=15,
    ).json()
    items = confirmed["receiveItems"]["items"]
    assert any(item["id"] == "char_1035_wisdel@game#9" for item in items)
    assert any(item["type"] == "PLAYER_AVATAR" for item in items)


def _account_user(uid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()
    return json.loads(row[0])


def _save_account_user(uid, user):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE accounts SET user_json=? WHERE uid=?",
            (json.dumps(user, ensure_ascii=False, separators=(",", ":")), int(uid)),
        )


def test_confirm_order_applies_and_persists_rewards(client, login, test_account):
    before = _account_user(test_account["uid"])
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 5399, "goodId": "GP_Once_3"},
        timeout=15,
    ).json()
    confirmed = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": created["orderId"]},
        timeout=15,
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["result"] == 0
    assert body["playerDataDelta"]["modified"]
    assert body["playerDataDelta"]["modified"]["status"]["gold"] == before["status"]["gold"] + 40000

    after = _account_user(test_account["uid"])
    assert after["status"]["gold"] == before["status"]["gold"] + 40000
    assert after["status"]["androidDiamond"] == before["status"]["androidDiamond"] + 13
    assert after["status"]["iosDiamond"] == before["status"].get("iosDiamond", 0)
    assert after["inventory"]["30032"] == int(before.get("inventory", {}).get("30032", 0) or 0) + 7
    with sqlite3.connect(DB_PATH) as conn:
        order = conn.execute(
            "SELECT status,granted,granted_at FROM orders WHERE order_id=?", (created["orderId"],)
        ).fetchone()
    assert order[0] == 1
    assert order[1] == 1
    assert order[2] is not None

    duplicate = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": created["orderId"]},
        timeout=15,
    ).json()
    assert duplicate["result"] == 0
    duplicate_user = _account_user(test_account["uid"])
    assert duplicate_user["status"]["gold"] == after["status"]["gold"]


def test_cash_order_grants_first_purchase_and_repeat_originium(client, login, test_account):
    before = _account_user(test_account["uid"])
    before_diamond = int(before["status"].get("androidDiamond", 0) or 0)

    first = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 6936, "goodId": "CS_1_r7"},
        timeout=15,
    ).json()
    first_confirm = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": first["orderId"]},
        timeout=15,
    ).json()
    assert first_confirm["result"] == 0
    assert first_confirm["receiveItems"]["items"] == [
        {"id": "4002", "type": "DIAMOND", "count": 3}
    ]
    assert first_confirm["playerDataDelta"]["modified"]["status"]["androidDiamond"] == before_diamond + 3
    assert _account_user(test_account["uid"])["status"]["androidDiamond"] == before_diamond + 3

    duplicate = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": first["orderId"]},
        timeout=15,
    ).json()
    assert duplicate["receiveItems"]["items"] == []
    assert _account_user(test_account["uid"])["status"]["androidDiamond"] == before_diamond + 3

    second = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 6936, "goodId": "CS_1_r7"},
        timeout=15,
    ).json()
    second_confirm = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": second["orderId"]},
        timeout=15,
    ).json()
    assert second_confirm["receiveItems"]["items"] == [
        {"id": "4002", "type": "DIAMOND", "count": 1}
    ]
    assert _account_user(test_account["uid"])["status"]["androidDiamond"] == before_diamond + 4

    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT granted_items_json FROM orders WHERE order_id=?", (first["orderId"],)
        ).fetchone()
    assert json.loads(row[0]) == [{"id": "4002", "type": "DIAMOND", "count": 3}]


def test_monthly_card_purchase_activates_subscription_and_daily_bonus(client, login, test_account):
    before = _account_user(test_account["uid"])
    before_status = before["status"]
    before_inventory = before.get("inventory") or {}
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 5466, "goodId": "GP_mCard_1"},
        timeout=15,
    ).json()
    confirmed = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": created["orderId"]},
        timeout=15,
    ).json()
    assert confirmed["result"] == 0
    items = confirmed["receiveItems"]["items"]
    assert {"id": "4002", "type": "DIAMOND", "count": 6} in items
    assert {"id": "4003", "type": "DIAMOND_SHD", "count": 200} in items
    assert {"id": "ap_supply_lt_80", "type": "AP_SUPPLY", "count": 1} in items

    after = _account_user(test_account["uid"])
    status = after["status"]
    assert status["androidDiamond"] == int(before_status.get("androidDiamond", 0) or 0) + 6
    assert status["diamondShard"] == int(before_status.get("diamondShard", 0) or 0) + 200
    assert status["monthlySubscriptionStartTime"] > 0
    assert status["monthlySubscriptionEndTime"] > status["monthlySubscriptionStartTime"]
    assert int((after.get("inventory") or {}).get("ap_supply_lt_80", 0)) == int(
        before_inventory.get("ap_supply_lt_80", 0)
    ) + 1
    assert after["localMonthlySub"]["lastGrantDay"] > 0

    duplicate = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": created["orderId"]},
        timeout=15,
    ).json()
    assert duplicate["receiveItems"]["items"] == []

    refreshed = _account_user(test_account["uid"])
    refreshed["localMonthlySub"]["lastGrantDay"] = 0
    _save_account_user(test_account["uid"], refreshed)
    before_checkin = _account_user(test_account["uid"])
    checkin = client.post(
        f"{client.base_url}/user/checkIn",
        headers=login["headers"],
        json={},
        timeout=15,
    ).json()
    assert {"id": "4003", "type": "DIAMOND_SHD", "count": 200} in checkin["subscriptionRewards"]
    after_checkin = _account_user(test_account["uid"])
    assert after_checkin["status"]["diamondShard"] == before_checkin["status"]["diamondShard"] + 200


def test_legacy_cash_product_id_uses_current_tier_reward(client, login):
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 5440, "goodId": "CS_2_r6"},
        timeout=15,
    ).json()
    confirmed = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": created["orderId"]},
        timeout=15,
    ).json()
    assert confirmed["result"] == 0
    assert confirmed["receiveItems"]["items"] == [
        {"id": "4002", "type": "DIAMOND", "count": 12}
    ]


def test_unconfigured_paid_product_is_not_marked_granted(client, login):
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 5399, "goodId": "GP_NOT_CONFIGURED"},
        timeout=15,
    ).json()
    confirmed = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": created["orderId"]},
        timeout=15,
    ).json()
    assert confirmed["result"] == 1
    assert confirmed["error"] == "shop reward not configured"
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT status,granted FROM orders WHERE order_id=?", (created["orderId"],)
        ).fetchone()
    assert row[0] == 0
    assert row[1] == 0


def test_ios_order_credits_only_ios_originium(client, login, test_account):
    before = _account_user(test_account["uid"])
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 6936, "goodId": "CS_1_r7", "platform": 2},
        timeout=15,
    ).json()
    confirmed = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": created["orderId"]},
        timeout=15,
    ).json()
    assert confirmed["result"] == 0
    after = _account_user(test_account["uid"])
    assert after["status"]["iosDiamond"] > int(before["status"].get("iosDiamond", 0) or 0)
    assert after["status"]["androidDiamond"] == int(before["status"].get("androidDiamond", 0) or 0)


def test_gift_catalog_has_contents_for_legacy_paid_products():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    catalog = json.loads((root / "backend" / "data" / "gift_packages.json").read_text(encoding="utf-8"))

    for good_id in ("GP_Once_384", "GP_Once_385", "GP_Once_523", "GP_gM_96_M_1"):
        row = catalog.get(good_id) or {}
        assert row.get("items"), good_id


def test_legacy_gift_package_purchase_grants_catalog_contents(client, login):
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 5398, "goodId": "GP_Once_384"},
        timeout=15,
    ).json()
    confirmed = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": created["orderId"]},
        timeout=15,
    ).json()
    assert confirmed["result"] == 0
    items = confirmed["receiveItems"]["items"]
    assert any(item["id"] == "3263" and item["type"] == "MATERIAL" for item in items)
