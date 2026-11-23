import json
import os
import sqlite3

from conftest import DB_PATH


def admin_login(client):
    return client.post(
        f"{client.base_url}/admin/login",
        data={
            "username": os.environ.get("ARK_ADMIN_USER", "sdars"),
            "password": os.environ.get("ARK_ADMIN_PASSWORD", "123456"),
        },
        allow_redirects=False,
        timeout=10,
    )


def _user(uid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()
    return json.loads(row[0])


def _save_user(uid, user):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE accounts SET user_json=? WHERE uid=?",
            (json.dumps(user, ensure_ascii=False, separators=(",", ":")), int(uid)),
        )


def _order(order_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    return dict(row) if row else None


def test_admin_can_create_and_reject_order(client, login, test_account):
    assert admin_login(client).status_code == 303
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 6937, "goodId": "CS_2_r7"},
        timeout=15,
    ).json()
    order_id = created["orderId"]
    before = _user(test_account["uid"])
    confirmed = client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": order_id},
        timeout=15,
    ).json()
    assert confirmed["result"] == 0
    after_grant = _user(test_account["uid"])
    assert after_grant["status"]["androidDiamond"] >= before["status"]["androidDiamond"]

    rejected = client.post(
        f"{client.base_url}/admin/orders/{order_id}/action",
        data={"action": "reject", "reason": "test reject"},
        allow_redirects=False,
        timeout=15,
    )
    assert rejected.status_code == 303
    row = _order(order_id)
    assert row["status"] == 2
    assert row["revoked"] == 1
    after_reject = _user(test_account["uid"])
    assert after_reject["status"]["androidDiamond"] <= after_grant["status"]["androidDiamond"]

    approved = client.post(
        f"{client.base_url}/admin/orders/{order_id}/action",
        data={"action": "approve", "reason": "test approve"},
        allow_redirects=False,
        timeout=15,
    )
    assert approved.status_code == 303
    row = _order(order_id)
    assert row["status"] == 1
    assert row["revoked"] == 0
    after_approve = _user(test_account["uid"])
    assert after_approve["status"]["androidDiamond"] == after_grant["status"]["androidDiamond"]


def test_voucher_auto_approves_scope_and_rejects_outside(client, login, test_account):
    assert admin_login(client).status_code == 303
    code = f"TEST-{test_account['uid']}"
    response = client.post(
        f"{client.base_url}/admin/vouchers",
        data={
            "name": "协议测试代金券",
            "code": code,
            "account_scope": "one",
            "uid": test_account["uid"],
            "scope_type": "goods",
            "goods": ["CS_3_r7"],
            "platform": "any",
            "max_amount_yuan": "98",
            "uses": "2",
        },
        allow_redirects=False,
        timeout=15,
    )
    assert response.status_code == 303
    before = _user(test_account["uid"])["status"]["androidDiamond"]
    approved = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 6938, "goodId": "CS_3_r7"},
        timeout=15,
    ).json()
    assert approved["result"] == 0
    row = _order(approved["orderId"])
    assert row["status"] == 1
    assert row["voucher_id"] is not None
    assert _user(test_account["uid"])["status"]["androidDiamond"] > before

    rejected = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 6941, "goodId": "CS_6_r7"},
        timeout=15,
    ).json()
    assert rejected["result"] == 1
    rejected_order = _order(rejected["orderId"])
    assert rejected_order["status"] == 2
    assert "代金券" in rejected_order["admin_note"]

    with sqlite3.connect(DB_PATH) as conn:
        voucher_id = conn.execute("SELECT id FROM vouchers WHERE code=?", (code,)).fetchone()[0]
    client.post(
        f"{client.base_url}/admin/vouchers/{voucher_id}/delete",
        allow_redirects=False,
        timeout=15,
    )


def test_order_page_and_resource_picker_api(client, login, test_account):
    assert admin_login(client).status_code == 303
    page = client.get(f"{client.base_url}/admin/orders", timeout=20)
    assert page.status_code == 200
    assert "订单管理" in page.text
    resources = client.get(
        f"{client.base_url}/admin/api/resources", params={"q": "char_1035_wisdel"}, timeout=20
    )
    assert resources.status_code == 200
    items = resources.json()["items"]
    assert any(item["id"] == "char_1035_wisdel" and item["name"] == "维什戴尔" for item in items)
    assert any(item["id"] == "char_1035_wisdel@game#9" for item in items)


def test_admin_bulk_create_and_bulk_reject(client, login, test_account):
    assert admin_login(client).status_code == 303
    response = client.post(
        f"{client.base_url}/admin/orders/create",
        data={
            "scope": "uids",
            "uids": str(test_account["uid"]),
            "good_ids": ["GP_Once_2"],
            "mode": "pending",
            "note": "bulk create test",
        },
        allow_redirects=False,
        timeout=20,
    )
    assert response.status_code == 303
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM orders WHERE uid=? AND good_id='GP_Once_2' ORDER BY created_at DESC LIMIT 1",
            (int(test_account["uid"]),),
        ).fetchone()
    assert row is not None
    assert row["status"] == 0
    assert row["origin"] == "admin"
    client.post(
        f"{client.base_url}/admin/orders/bulk",
        data={"order_ids": [row["order_id"]], "action": "reject", "reason": "bulk reject"},
        allow_redirects=False,
        timeout=20,
    )
    updated = _order(row["order_id"])
    assert updated["status"] == 2


def test_reject_records_shortfall_and_approve_clears_it(client, login, test_account):
    assert admin_login(client).status_code == 303
    created = client.post(
        f"{client.base_url}/pay/createOrder",
        headers=login["headers"],
        json={"storeId": 6939, "goodId": "CS_4_r7"},
        timeout=15,
    ).json()
    order_id = created["orderId"]
    client.post(
        f"{client.base_url}/pay/confirmOrder",
        headers=login["headers"],
        json={"orderId": order_id},
        timeout=15,
    )
    user = _user(test_account["uid"])
    user["status"]["androidDiamond"] = 0
    _save_user(test_account["uid"], user)
    client.post(
        f"{client.base_url}/admin/orders/{order_id}/action",
        data={"action": "reject", "reason": "debt test"},
        allow_redirects=False,
        timeout=15,
    )
    row = _order(order_id)
    assert row["revoke_debt_json"]
    assert _user(test_account["uid"])["status"]["androidDiamond"] == 0
    client.post(
        f"{client.base_url}/admin/orders/{order_id}/action",
        data={"action": "approve", "reason": "debt test approve"},
        allow_redirects=False,
        timeout=15,
    )
    assert _user(test_account["uid"])["status"]["androidDiamond"] > 0
