import json
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from .config import APP_CODE, BASE_DIR
from .db import connect, now_ts
from .grants import apply_items, empty_delta, merge_delta, normalize_platform
from . import gift_catalog, grants, monthly_card, vouchers


PRODUCT_PATH = BASE_DIR / "fixtures" / "product_list.json"
SHOP_DIR = BASE_DIR / "fixtures" / "shop"
_lock = threading.Lock()
_confirm_lock = threading.Lock()
_products = None
_shop_goods = None
_shop_nodes = None
_cash_goods = None


def order_platform(value):
    return normalize_platform(value)


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
    global _shop_goods, _shop_nodes
    with _lock:
        if _shop_goods is None or _shop_nodes is None:
            found = {}
            nodes = {}
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
                                nodes.setdefault(str(good_id), node)
                            stack.extend(node.values())
                        elif isinstance(node, list):
                            stack.extend(node)
            _shop_goods = found
            _shop_nodes = nodes
        return _shop_goods


def shop_good(good_id):
    shop_goods()
    return (_shop_nodes or {}).get(str(good_id or "")) or {}


def card_id_for_good(good_id):
    card_id = shop_good(good_id).get("cardId")
    return str(card_id) if card_id else ""


def cash_goods():
    global _cash_goods
    with _lock:
        if _cash_goods is None:
            payload = None
            path = SHOP_DIR / "CashGoodList.json"
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    payload = None
            entries = payload.get("goodList") if isinstance(payload, dict) else payload
            found = {}
            for entry in entries or []:
                if isinstance(entry, dict) and entry.get("goodId"):
                    found[str(entry["goodId"])] = entry
            _cash_goods = found
        return _cash_goods


def cash_good_entry(good_id):
    good_id = str(good_id or "")
    entry = cash_goods().get(good_id)
    if entry:
        return entry
    parts = good_id.split("_")
    if len(parts) >= 3 and parts[0] == "CS":
        prefix = f"CS_{parts[1]}_"
        for candidate_id, candidate in cash_goods().items():
            if candidate_id.startswith(prefix):
                return candidate
    return None


def is_cash_good(good_id):
    return cash_good_entry(good_id) is not None


def cash_good_item(good_id, first_purchase=False):
    entry = cash_good_entry(good_id)
    if not entry:
        return None
    if first_purchase:
        amount = int(entry.get("doubleCount") or 0)
    else:
        amount = int(entry.get("diamondNum") or 0) + int(entry.get("plusNum") or 0)
    if amount <= 0:
        amount = int(entry.get("diamondNum") or 0)
    if amount <= 0:
        return None
    return {"id": "4002", "type": "DIAMOND", "count": amount}


def good_items(good_id):
    if is_cash_good(good_id):
        item = cash_good_item(good_id, first_purchase=False)
        return [item] if item else []
    good_id = str(good_id or "")
    items = shop_goods().get(good_id)
    if items:
        return list(items)
    if good_id.startswith("GP_"):
        return gift_catalog.items(good_id)
    return []


def _first_cash_purchase(conn, order):
    row = conn.execute(
        """SELECT COUNT(*) AS c FROM orders
           WHERE account_id=? AND good_id=? AND status=1
             AND (created_at < ? OR (created_at = ? AND order_id < ?))""",
        (
            order["account_id"],
            order["good_id"],
            order["created_at"],
            order["created_at"],
            order["order_id"],
        ),
    ).fetchone()
    return int(row["c"] or 0) == 0


def order_items(order, conn=None):
    good_id = str(order.get("good_id") or "")
    if not is_cash_good(good_id):
        return good_items(good_id)
    if conn is None:
        with connect() as local_conn:
            first = _first_cash_purchase(local_conn, order)
    else:
        first = _first_cash_purchase(conn, order)
    item = cash_good_item(good_id, first_purchase=first)
    return [item] if item else []


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


def create_order(
    account,
    store_id=None,
    good_id=None,
    platform=None,
    origin="client",
    created_by=None,
    evaluate_voucher=True,
    replace_pending=True,
):
    product = find_product(store_id, good_id)
    if not product:
        return None
    ts = now_ts()
    # The in-game client treats order ids as opaque strings. Microsecond
    # precision prevents collisions when protocol tests create several orders
    # inside the same second.
    order_id = f"{int(time.time() * 1_000_000)}{account['uid']}"
    resolved_platform = (
        order_platform(platform)
        or order_platform(account.get("platform"))
        or order_platform((json.loads(account.get("user_json") or "{}") or {}).get("localPlatform"))
        or "android"
    )
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
        "platform": resolved_platform,
        "raw_json": json.dumps(
            {"storeId": store_id, "goodId": good_id, "platform": resolved_platform},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "origin": str(origin or "client"),
        "created_by": created_by,
    }
    with connect() as conn:
        if replace_pending:
            conn.execute(
                "DELETE FROM orders WHERE account_id=? AND status=0 AND origin='client'",
                (account["id"],),
            )
        else:
            conn.execute(
                "DELETE FROM orders WHERE account_id=? AND status=0 AND good_id=?",
                (account["id"], order["good_id"]),
            )
        conn.execute(
            """INSERT INTO orders(order_id,account_id,uid,token,store_id,good_id,product_name,amount,status,created_at,paid_at,raw_json,platform,origin,created_by)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                order["order_id"], order["account_id"], order["uid"], order["token"],
                order["store_id"], order["good_id"], order["product_name"], order["amount"],
                0, order["created_at"], None, order["raw_json"], order["platform"],
                order["origin"], order["created_by"],
            ),
        )
    if evaluate_voucher:
        decision = vouchers.evaluate(account, order)
        if decision["action"] == "approve":
            voucher = decision["voucher"]
            if vouchers.consume(voucher["id"]):
                with connect() as conn:
                    conn.execute(
                        "UPDATE orders SET voucher_id=? WHERE order_id=?",
                        (int(voucher["id"]), order["order_id"]),
                    )
                result = confirm_order(account, order["order_id"], source="voucher")
                if result.get("result") == 0:
                    order["status"] = 1
                    order["voucher_id"] = voucher["id"]
                else:
                    vouchers.release(voucher["id"])
        elif decision["action"] == "reject":
            with connect() as conn:
                conn.execute(
                    "UPDATE orders SET status=2,admin_note=? WHERE order_id=?",
                    (decision.get("reason") or "代金券范围外", order["order_id"]),
                )
            order["status"] = 2
            order["reject_reason"] = decision.get("reason") or "代金券范围外"
    return order


def latest_order(account):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE account_id=? ORDER BY created_at DESC, order_id DESC LIMIT 1",
            (account["id"],),
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


def sdk_app_product(account, platform=None):
    order = latest_order(account)
    if not order:
        return {"status": 1, "msg": "order not found", "type": "A"}
    resolved = order_platform(platform)
    if resolved and resolved != order.get("platform"):
        with connect() as conn:
            conn.execute(
                "UPDATE orders SET platform=? WHERE order_id=?",
                (resolved, order["order_id"]),
            )
        order["platform"] = resolved
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
            "UPDATE orders SET status=1,paid_at=COALESCE(paid_at,?) WHERE order_id=? AND status=0",
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
        "status": 0 if int(order["status"] or 0) == 1 else (102 if int(order["status"] or 0) == 2 else 101),
        "msg": "已支付已回调" if int(order["status"] or 0) == 1 else ("订单已驳回" if int(order["status"] or 0) == 2 else "未支付"),
    }


def confirm_order(account, order_id, source="client"):
    with _confirm_lock:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
            if not row or row["account_id"] != account["id"]:
                return {"result": 2, "error": "order not found"}
            order = dict(row)
            if int(order.get("status") or 0) == 2 or int(order.get("revoked") or 0):
                return {
                    "result": 1,
                    "error": "order rejected",
                    "goodId": order.get("good_id"),
                }
            account_row = conn.execute(
                "SELECT * FROM accounts WHERE id=?", (order["account_id"],)
            ).fetchone()
            if not account_row:
                return {"result": 2, "error": "account not found"}
            items = order_items(order, conn=conn)
            card_id = card_id_for_good(order["good_id"])
            if not items and not card_id:
                return {
                    "result": 1,
                    "goodId": order["good_id"],
                    "error": "shop reward not configured",
                }
            delta = empty_delta()
            applied = False
            granted_items = []
            platform = (
                order_platform(order.get("platform"))
                or order_platform(account_row["platform"] if "platform" in account_row.keys() else None)
            )
            granted = int(order.get("granted", 0) or 0)
            has_ledger = bool(order.get("granted_items_json"))
            repair_missing_ledger = granted and not has_ledger and bool(items)
            if not granted or repair_missing_ledger:
                applied = True
                user = json.loads(account_row["user_json"])
                _, delta = apply_items(user, items, source="shop", platform=platform)
                granted_items = list(items)
                if card_id:
                    daily, monthly_delta, _ = monthly_card.activate(
                        user, card_id, now=now_ts(), platform=platform
                    )
                    merge_delta(delta, monthly_delta)
                    granted_items.extend(daily)
                if str(order["good_id"]).startswith("GP_"):
                    gift_catalog.record_purchase(user, order["good_id"], 1)
                    delta.setdefault("modified", {})["shop"] = user.get("shop", {})
                conn.execute(
                    "UPDATE accounts SET user_json=? WHERE id=?",
                    (
                        json.dumps(user, ensure_ascii=False, separators=(",", ":")),
                        account_row["id"],
                    ),
                )
                conn.execute(
                    """UPDATE orders
                       SET status=1, paid_at=COALESCE(paid_at,?), granted=1,
                           granted_at=COALESCE(granted_at,?), granted_items_json=?
                       WHERE order_id=?""",
                    (
                        now_ts(),
                        now_ts(),
                        json.dumps(granted_items, ensure_ascii=False, separators=(",", ":")),
                        order_id,
                    ),
                )
            elif not has_ledger:
                conn.execute(
                    "UPDATE orders SET granted_items_json=? WHERE order_id=?",
                    (json.dumps(items, ensure_ascii=False, separators=(",", ":")), order_id),
                )
            return {
                "result": 0,
                "goodId": order["good_id"],
                "receiveItems": {"items": granted_items if applied else [], "checkInItems": []},
                "orderIdList": [order_id],
                "playerDataDelta": delta,
            }


def _ledger_items(order):
    try:
        ledger = json.loads(order.get("granted_items_json") or "[]")
    except Exception:
        ledger = []
    if isinstance(ledger, list) and ledger:
        return ledger
    return order_items(order)


def _order_platform(order, account_row=None, user=None):
    return (
        order_platform(order.get("platform"))
        or order_platform(account_row["platform"] if account_row is not None and "platform" in account_row.keys() else None)
        or order_platform((user or {}).get("localPlatform"))
        or "android"
    )


def admin_approve_order(order_id, actor=None):
    with _confirm_lock:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
            if not row:
                return {"result": 2, "error": "order not found"}
            order = dict(row)
            account_row = conn.execute("SELECT * FROM accounts WHERE id=?", (order["account_id"],)).fetchone()
            if not account_row:
                return {"result": 2, "error": "account not found"}
            user = json.loads(account_row["user_json"])
            platform = _order_platform(order, account_row, user)
            items = _ledger_items(order)
            card_id = card_id_for_good(order["good_id"])
            granted = int(order.get("granted", 0) or 0)
            revoked = int(order.get("revoked", 0) or 0)
            if revoked:
                try:
                    revoked_debt = json.loads(order.get("revoke_debt_json") or "{}")
                except Exception:
                    revoked_debt = {}
                grants.clear_debt_map(user, revoked_debt)
            if not granted or revoked:
                if revoked and order.get("voucher_id"):
                    vouchers.consume(order["voucher_id"])
                apply_items(user, items, source="admin-order", platform=platform)
                if card_id:
                    monthly_card.adjust(user, 30)
                if str(order["good_id"]).startswith("GP_"):
                    gift_catalog.record_purchase(user, order["good_id"], 1)
            conn.execute(
                """UPDATE orders SET status=1,paid_at=COALESCE(paid_at,?),granted=1,granted_at=COALESCE(granted_at,?),
                   granted_items_json=?,revoked=0,revoked_at=NULL,revoked_items_json=NULL,
                   revoke_debt_json=NULL,admin_note=COALESCE(admin_note,'') WHERE order_id=?""",
                (
                    now_ts(),
                    now_ts(),
                    json.dumps(items, ensure_ascii=False, separators=(",", ":")),
                    order_id,
                ),
            )
            conn.execute(
                "UPDATE accounts SET user_json=? WHERE id=?",
                (json.dumps(user, ensure_ascii=False, separators=(",", ":")), account_row["id"]),
            )
    return {"result": 0, "orderId": order_id, "items": items}


def admin_reject_order(order_id, actor=None, reason=""):
    with _confirm_lock:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
            if not row:
                return {"result": 2, "error": "order not found"}
            order = dict(row)
            account_row = conn.execute("SELECT * FROM accounts WHERE id=?", (order["account_id"],)).fetchone()
            if not account_row:
                return {"result": 2, "error": "account not found"}
            user = json.loads(account_row["user_json"])
            platform = _order_platform(order, account_row, user)
            items = _ledger_items(order)
            card_id = card_id_for_good(order["good_id"])
            granted = int(order.get("granted", 0) or 0)
            revoked_items = []
            debt = {}
            if granted and int(order.get("revoked", 0) or 0) == 0:
                revoked_items, debt, _ = grants.revoke_items(
                    user, items, source="admin-reject", platform=platform
                )
                if card_id:
                    monthly_card.adjust(user, -30)
                if str(order["good_id"]).startswith("GP_"):
                    gift_catalog.adjust_purchase(user, order["good_id"], -1)
                if order.get("voucher_id"):
                    vouchers.release(order["voucher_id"])
            conn.execute(
                """UPDATE orders SET status=2,revoked=?,revoked_at=?,revoked_items_json=?,
                   revoke_debt_json=?,admin_note=? WHERE order_id=?""",
                (
                    1 if granted else 0,
                    now_ts() if granted else None,
                    json.dumps(revoked_items, ensure_ascii=False, separators=(",", ":")) if granted else None,
                    json.dumps(debt, ensure_ascii=False, separators=(",", ":")) if debt else None,
                    str(reason or "管理员驳回"),
                    order_id,
                ),
            )
            conn.execute(
                "UPDATE accounts SET user_json=? WHERE id=?",
                (json.dumps(user, ensure_ascii=False, separators=(",", ":")), account_row["id"]),
            )
    return {"result": 0, "orderId": order_id, "revokedItems": revoked_items, "debt": debt}


def admin_set_pending(order_id, note=""):
    with connect() as conn:
        conn.execute(
            "UPDATE orders SET status=0,admin_note=? WHERE order_id=?",
            (str(note or "管理员重置为待处理"), order_id),
        )
    return {"result": 0, "orderId": order_id}


def backfill_paid_order_grants(limit=1000):
    """Deliver paid orders, including legacy cash orders that were marked granted without items."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT order_id,account_id FROM orders
               WHERE status=1 AND (
                   granted=0 OR
                   (granted=1 AND granted_items_json IS NULL AND (
                       substr(good_id,1,3)='CS_' OR substr(good_id,1,3)='GP_'
                   ))
               )
               ORDER BY created_at LIMIT ?""",
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


def reconcile_gift_purchase_state():
    """Reflect paid gift orders in player.shop.GP so the client marks sold packs."""
    updated = 0
    with connect() as conn:
        accounts = conn.execute("SELECT id,user_json FROM accounts").fetchall()
        for account in accounts:
            orders = conn.execute(
                """SELECT good_id, COUNT(*) AS c FROM orders
                   WHERE account_id=? AND status=1 AND good_id LIKE 'GP_%'
                   GROUP BY good_id""",
                (account["id"],),
            ).fetchall()
            if not orders:
                continue
            user = json.loads(account["user_json"])
            changed = False
            for order in orders:
                good_id = str(order["good_id"] or "")
                count = int(order["c"] or 0)
                if not good_id or count <= 0:
                    continue
                current = gift_catalog.purchase_count(user, good_id)
                if current < count:
                    gift_catalog.set_purchase_count(user, good_id, count)
                    changed = True
            if changed:
                conn.execute(
                    "UPDATE accounts SET user_json=? WHERE id=?",
                    (json.dumps(user, ensure_ascii=False, separators=(",", ":")), account["id"]),
                )
                updated += 1
    return updated


def new_bill_id():
    return uuid.uuid4().hex
