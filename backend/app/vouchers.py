import json
import secrets

from .db import connect, now_ts


def _json(value, default):
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
        return parsed if isinstance(parsed, (dict, list)) else default
    except Exception:
        return default


def new_code(prefix="VC"):
    return f"{prefix}-{secrets.token_hex(4).upper()}"


def create_voucher(
    name,
    code=None,
    account_id=None,
    account_uid=None,
    scope=None,
    max_amount=0,
    uses=1,
    start_at=0,
    end_at=0,
    status=1,
):
    now = now_ts()
    code = str(code or "").strip() or new_code()
    scope = scope if isinstance(scope, dict) else {}
    uses = int(uses if uses is not None else 1)
    if uses == 0:
        uses = -1
    with connect() as conn:
        cursor = conn.execute(
            """INSERT INTO vouchers(name,code,account_id,account_uid,scope_json,max_amount,
               uses_total,uses_remaining,start_at,end_at,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(name or "代金券"),
                code,
                account_id,
                account_uid,
                json.dumps(scope, ensure_ascii=False, separators=(",", ":")),
                int(max_amount or 0),
                uses,
                uses,
                int(start_at or 0),
                int(end_at or 0),
                int(status or 1),
                now,
                now,
            ),
        )
        return cursor.lastrowid


def list_vouchers():
    with connect() as conn:
        rows = conn.execute(
            """SELECT v.*, a.uid AS owner_uid, a.phone AS owner_phone, a.nickname AS owner_nickname
               FROM vouchers v LEFT JOIN accounts a ON a.id=v.account_id
               ORDER BY v.id DESC"""
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["scope"] = _json(item.get("scope_json"), {})
        result.append(item)
    return result


def get_voucher(voucher_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM vouchers WHERE id=?", (int(voucher_id),)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["scope"] = _json(item.get("scope_json"), {})
    return item


def set_status(voucher_id, status):
    with connect() as conn:
        conn.execute(
            "UPDATE vouchers SET status=?,updated_at=? WHERE id=?",
            (int(status), now_ts(), int(voucher_id)),
        )


def delete_voucher(voucher_id):
    with connect() as conn:
        conn.execute("DELETE FROM vouchers WHERE id=?", (int(voucher_id),))


def _category(order):
    good_id = str(order.get("good_id") or "")
    product_name = str(order.get("product_name") or "")
    if good_id.startswith("CS_"):
        return "cash"
    if good_id == "GP_mCard_1" or "月卡" in product_name:
        return "monthly"
    if good_id.startswith("GP_"):
        return "gift"
    return "other"


def matches(voucher, order, now=None):
    now = int(now or now_ts())
    if int(voucher.get("status") or 0) != 1:
        return False
    uses = int(voucher.get("uses_remaining") or 0)
    if uses == 0:
        return False
    start = int(voucher.get("start_at") or 0)
    end = int(voucher.get("end_at") or 0)
    if start and now < start:
        return False
    if end and now > end:
        return False
    if voucher.get("account_id") is not None and int(voucher["account_id"]) != int(order.get("account_id") or 0):
        return False
    scope = voucher.get("scope") or _json(voucher.get("scope_json"), {})
    goods = {str(value) for value in scope.get("goods") or [] if value}
    if goods and str(order.get("good_id") or "") not in goods:
        return False
    categories = {str(value) for value in scope.get("categories") or [] if value}
    if categories and "all" not in categories and _category(order) not in categories:
        return False
    platform = str(scope.get("platform") or "").lower()
    if platform and platform not in {"any", "all"} and platform != str(order.get("platform") or ""):
        return False
    min_amount = int(scope.get("min_amount") or 0)
    if min_amount and int(order.get("amount") or 0) < min_amount:
        return False
    max_amount = int(voucher.get("max_amount") or 0)
    if max_amount and int(order.get("amount") or 0) > max_amount:
        return False
    return True


def active_for(account, now=None):
    now = int(now or now_ts())
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM vouchers
               WHERE status=1 AND (account_id=? OR account_id IS NULL)
                 AND (start_at=0 OR start_at<=?)
                 AND (end_at=0 OR end_at>=?)
                 AND (uses_remaining<>0)
               ORDER BY id ASC""",
            (account["id"], now, now),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["scope"] = _json(item.get("scope_json"), {})
        result.append(item)
    return result


def evaluate(account, order, now=None):
    active = active_for(account, now=now)
    for voucher in active:
        if matches(voucher, order, now=now):
            return {"action": "approve", "voucher": voucher}
    if active:
        return {
            "action": "reject",
            "voucher": None,
            "reason": "该订单超出当前代金券适用范围",
        }
    return {"action": "pending", "voucher": None}


def consume(voucher_id):
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT uses_remaining FROM vouchers WHERE id=?", (int(voucher_id),)).fetchone()
        if not row:
            return False
        remaining = int(row["uses_remaining"] or 0)
        if remaining == 0:
            return False
        if remaining > 0:
            conn.execute(
                "UPDATE vouchers SET uses_remaining=uses_remaining-1,updated_at=? WHERE id=?",
                (now_ts(), int(voucher_id)),
            )
    return True


def release(voucher_id):
    with connect() as conn:
        conn.execute(
            """UPDATE vouchers SET uses_remaining=CASE
                   WHEN uses_remaining<0 THEN uses_remaining
                   ELSE uses_remaining+1 END,updated_at=? WHERE id=?""",
            (now_ts(), int(voucher_id)),
        )


def scope_summary(scope):
    scope = scope if isinstance(scope, dict) else {}
    parts = []
    goods = scope.get("goods") or []
    if goods:
        parts.append("商品: " + ", ".join(str(value) for value in goods[:4]) + ("…" if len(goods) > 4 else ""))
    categories = scope.get("categories") or []
    labels = {"cash": "源石直充", "gift": "组合包", "monthly": "月卡", "other": "其他", "all": "全部商品"}
    if categories:
        parts.append("范围: " + ", ".join(labels.get(value, value) for value in categories))
    platform = str(scope.get("platform") or "")
    if platform and platform not in {"any", "all"}:
        parts.append("平台: " + ("iOS" if platform == "ios" else "Android"))
    return " · ".join(parts) or "全部商品"
