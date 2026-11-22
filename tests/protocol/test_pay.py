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
        json={"storeId": 5399, "goodId": "GP_Once_385"},
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
