import json
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from .config import APP_CODE, BASE_DIR
from .db import connect, now_ts
from .grants import apply_items, empty_delta


PRODUCT_PATH = BASE_DIR / "fixtures" / "product_list.json"
SHOP_DIR = BASE_DIR / "fixtures" / "shop"
_lock = threading.Lock()
_confirm_lock = threading.Lock()
_products = None
_shop_goods = None


def products():
    global _products
    with _lock:
        if _products is None:
            payload = json.loads(Path(PRODUCT_PATH).read_text(encoding="utf-8"))
            _products = payload.get("productList", [])
        return _products


def find_product(store_id=None, good_id=None):
    for item in products():
        if store_id is not None and int(item.get("store_id", -1)) == int(store_id):
            return item
    if good_id:
        for item in products():
            if item.get("product_id") == good_id or item.get("appstore_id") == good_id:
                return item
    return products()[0] if products() else None


def shop_goods():
    global _shop_goods
    with _lock:
        if _shop_goods is None:
            found = {}
            if SHOP_DIR.exists():
                for path in SHOP_DIR.glob("*.json"):
                    try:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    stack = [payload]
                    while stack:
                        node = stack.pop()
                        if isinstance(node, dict):
                            good_id = node.get("goodId")
                            if good_id and isinstance(node.get("items"), list):
                                found.setdefault(str(good_id), node["items"])
                            stack.extend(node.values())
                        elif isinstance(node, list):
                            stack.extend(node)
            _shop_goods = found
        return _shop_goods


def good_items(good_id):
    return list(shop_goods().get(str(good_id or ""), []))


def _extension(order):
    return {
        "appCode": APP_CODE,
        "amount": order["amount"],
        "productName": order["product_name"],
        "uid": order["uid"],
        "outOrderId": order["order_id"],
        "ts": order["created_at"],
        "extension": {
            "appStoreProductId": order["good_id"],
            "harmonyProductId": order["good_id"],
            "platform": 2,
            "sign": "0cf6ed10808afa887cab5ab747e11e1e",
        },
    }


def create_order(account, store_id=None, good_id=None):
    product = find_product(store_id, good_id)
    if not product:
        return None
    ts = now_ts()
    # The in-game client treats order ids as opaque strings. Microsecond
    # precision prevents collisions when protocol tests create several orders
    # inside the same second.
    order_id = f"{int(time.time() * 1_000_000)}{account['uid']}"
    order = {
        "order_id": order_id,
        "account_id": account["id"],
        "uid": account["uid"],
        "token": account["token"],
        "store_id": int(product["store_id"]),
        "good_id": good_id or product["product_id"],
        "product_name": product["name"],
        "amount": int(product["price"]),
        "status": 0,
        "created_at": ts,
        "paid_at": None,
        "raw_json": json.dumps({"storeId": store_id, "goodId": good_id}, ensure_ascii=False),
    }
    with connect() as conn:
        conn.execute("DELETE FROM orders WHERE account_id=? AND status=0", (account["id"],))
        conn.execute(
            """INSERT INTO orders(order_id,account_id,uid,token,store_id,good_id,product_name,amount,status,created_at,paid_at,raw_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                order["order_id"], order["account_id"], order["uid"], order["token"],
                order["store_id"], order["good_id"], order["product_name"], order["amount"],
                0, order["created_at"], None, order["raw_json"],
            ),
        )
    return order


def latest_order(account):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE account_id=? ORDER BY created_at DESC LIMIT 1", (account["id"],)
        ).fetchone()
    return dict(row) if row else None


def order_by_id(order_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    return dict(row) if row else None


def create_order_response(order):
    if not order:
        return {"result": 2, "error": "product not found"}
    extension = json.dumps(_extension(order), ensure_ascii=False, separators=(",", ":"))
    return {
        "result": 0,
        "orderId": order["order_id"],
        "extension": extension,
        "orderIdList": [],
        "alertMinor": 0,
        "errMsg": "",
        "playerDataDelta": {"deleted": {}, "modified": {}},
    }


def sdk_app_product(account):
    order = latest_order(account)
    if not order:
        return {"status": 1, "msg": "order not found", "type": "A"}
    return {
        "data": {
            "orderId": order["order_id"],
            "amount": order["amount"],
            "productName": order["product_name"],
            "payment": [
                {"key": "alipay", "recommend": True, "discount": False},
                {"key": "wechat", "recommend": False, "discount": False},
            ],
            "displayMode": 0,
        },
        "msg": "ok",
        "status": 0,
        "type": "A",
    }


def sdk_bill(account, channel):
    order = latest_order(account)
    if not order:
        return {"status": 1, "msg": "order not found", "type": "A"}
    success_url = f"http://127.0.0.1:8443/pay/success?orderId={order['order_id']}"
    qs = urlencode(
        {
            "alipay_sdk": "1",
            "app_id": "local",
            "charset": "utf-8",
            "method": "alipay.trade.app.pay",
            "out_trade_no": order["order_id"],
            "return_url": success_url,
            "sign": "local",
            "sign_type": "RSA2",
            "timestamp": str(order["created_at"]),
            "version": "1.0",
        }
    )
    return {
        "data": {
            "extension": {
                "qrCodeUrl": success_url,
                "channel": channel,
                "qs": qs,
                "returnUrl": success_url,
                "appid": "local",
                "partnerid": "local",
                "prepayid": "local",
                "package": "Sign=WXPay",
                "noncestr": "local",
                "timestamp": "0",
                "sign": "local",
            },
            "billOrderId": order["order_id"],
            "expireTime": 600,
        },
        "msg": "ok",
        "status": 0,
        "type": "A",
    }


def mark_paid(order_id):
    with connect() as conn:
        conn.execute(
            "UPDATE orders SET status=1,paid_at=COALESCE(paid_at,?) WHERE order_id=?",
            (now_ts(), order_id),
        )
    return order_by_id(order_id)


def alipay_page_pay(account, order_id):
    """Return the HGSDK app-product page-pay payload.

    The local deployment keeps Alipay's URL shape, but the device hosts entry
    and mitm rule turn openapi.alipay.com into a local success redirect.
    """
    order = order_by_id(order_id)
    if not order or order["account_id"] != account["id"]:
        return {"status": 1, "msg": "order not found", "type": "A"}
    success_url = f"http://127.0.0.1:8443/pay/success?orderId={order_id}"
    qs = urlencode(
        {
            "alipay_sdk": "1",
            "app_id": "local",
            "charset": "utf-8",
            "method": "alipay.trade.app.pay",
            "out_trade_no": order_id,
            "return_url": success_url,
            "sign": "local",
            "sign_type": "RSA2",
            "timestamp": str(order["created_at"]),
            "version": "1.0",
        }
    )
    return {
        "qs": qs,
        "pagePay": True,
        "orderId": order_id,
        "amount": order["amount"],
        "productName": order["product_name"],
    }


def confirm_alipay(account, order_id, result="", success=True):
    order = order_by_id(order_id)
    if not order or order["account_id"] != account["id"]:
        return {"status": 1, "msg": "order not found", "type": "A"}
    if success:
        mark_paid(order_id)
    return {
        "status": 0 if success else 2,
        "msg": "OK" if success else "未支付",
        "type": "A",
        "data": {
            "orderId": order_id,
            "result": result,
            "isConfirmed": bool(success),
        },
    }


def order_state(order_id):
    order = order_by_id(order_id)
    if not order:
        return {"status": 1, "msg": "order not found", "type": "A"}
    return {
        "data": {
            "productName": order["product_name"],
            "amount": order["amount"],
            "createTime": order["created_at"],
            "productType": 2,
            "appCode": APP_CODE,
            "orderId": order_id,
            "orderType": 0,
            "pc": 1,
        },
        "type": "A",
        "status": 0 if order["status"] else 101,
        "msg": "已支付已回调" if order["status"] else "未支付",
    }


def confirm_order(account, order_id):
    with _confirm_lock:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
            if not row or row["account_id"] != account["id"]:
                return {"result": 2, "error": "order not found"}
            order = dict(row)
            account_row = conn.execute(
                "SELECT * FROM accounts WHERE id=?", (order["account_id"],)
            ).fetchone()
            if not account_row:
                return {"result": 2, "error": "account not found"}
            items = good_items(order["good_id"])
            delta = empty_delta()
            if not int(order.get("granted", 0) or 0):
                user = json.loads(account_row["user_json"])
                _, delta = apply_items(user, items, source="shop")
                conn.execute(
                    "UPDATE accounts SET user_json=? WHERE id=?",
                    (
                        json.dumps(user, ensure_ascii=False, separators=(",", ":")),
                        account_row["id"],
                    ),
                )
                conn.execute(
                    "UPDATE orders SET status=1,paid_at=COALESCE(paid_at,?),granted=1,granted_at=? WHERE order_id=?",
                    (now_ts(), now_ts(), order_id),
                )
            return {
                "result": 0,
                "goodId": order["good_id"],
                "receiveItems": {"items": items, "checkInItems": []},
                "orderIdList": [order_id],
                "playerDataDelta": delta,
            }


def backfill_paid_order_grants(limit=1000):
    """Deliver paid orders recorded before reward persistence was added."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT order_id,account_id FROM orders WHERE status=1 AND granted=0 ORDER BY created_at LIMIT ?",
            (int(limit),),
        ).fetchall()
    repaired = 0
    for row in rows:
        with connect() as conn:
            account = conn.execute("SELECT * FROM accounts WHERE id=?", (row["account_id"],)).fetchone()
        if not account:
            continue
        result = confirm_order(dict(account), row["order_id"])
        if result.get("result") == 0:
            repaired += 1
    return repaired


def new_bill_id():
    return uuid.uuid4().hex
