import json
import threading

from .config import BASE_DIR
from .db import connect, now_ts
from . import game_data
from .grants import apply_items, empty_delta


ROOT = BASE_DIR.parent
MAIL_DIR = ROOT / "server" / "data" / "mail"
FIRST_MAIL_ID = 751
_lock = threading.Lock()


def _json(value, default):
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _items(raw):
    if isinstance(raw, str):
        raw = _json(raw, [])
    if isinstance(raw, dict):
        raw = raw.get("items") or []
    result = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or item.get("itemId") or "")
        item_type = str(item.get("type") or item.get("itemType") or "").upper()
        if item_id and not item_type:
            item_type = game_data.item_type(item_id)
        try:
            count = int(item.get("count") or 1)
        except (TypeError, ValueError):
            continue
        if item_id and item_type and count > 0:
            result.append({"id": item_id, "type": item_type, "count": count})
    return result


def _template_payload(row):
    if not row:
        return None
    data = dict(row)
    data["items"] = _items(data.get("items_json"))
    data["style"] = _json(data.get("style_json"), {})
    return data


def seed_templates():
    templates = []
    seed_path = MAIL_DIR / "mail_templates.json"
    if seed_path.exists():
        payload = _json(seed_path.read_text(encoding="utf-8"), {})
        templates = payload.get("templates") or []
    if not templates:
        templates = [
            {
                "name": "welcome_2026",
                "from": "penguin_logistics",
                "subject": "罗德岛补给",
                "content": "博士：\n\n欢迎回到罗德岛。随信附上初期补给，请查收。\n\n[罗德岛后勤部]",
                "expireDays": 365,
                "items": [
                    {"id": "7001", "type": "TKT_RECRUIT", "count": 5},
                    {"id": "7002", "type": "TKT_INST_FIN", "count": 5},
                    {"id": "4001", "type": "GOLD", "count": 20000},
                ],
            },
            {
                "name": "maintain_compensate",
                "from": "rhodes_island",
                "subject": "维护补偿",
                "content": "尊敬的博士：\n\n本次维护已完成，补偿已随信附上。\n\n[明日方舟运营组]",
                "expireDays": 30,
                "items": [
                    {"id": "4002", "type": "DIAMOND", "count": 5},
                    {"id": "4001", "type": "GOLD", "count": 20000},
                ],
            },
        ]
    now = now_ts()
    inserted = 0
    with connect() as conn:
        for item in templates:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            if int(item.get("type") or 1) == 2:
                continue
            exists = conn.execute("SELECT id FROM mail_templates WHERE name=?", (item["name"],)).fetchone()
            if exists:
                continue
            conn.execute(
                """INSERT INTO mail_templates(name,sender,subject,content,items_json,expire_days,style_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    str(item["name"]),
                    str(item.get("from") or "penguin_logistics"),
                    str(item.get("subject") or item["name"]),
                    str(item.get("content") or ""),
                    json.dumps(_items(item.get("items")), ensure_ascii=False, separators=(",", ":")),
                    int(item.get("expireDays") or 365),
                    json.dumps(item.get("style") or {"route": 0, "banner": ""}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            inserted += 1
    return inserted


def templates():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM mail_templates ORDER BY id ASC").fetchall()
    return [_template_payload(row) for row in rows]


def template(template_id=None, name=None):
    with connect() as conn:
        if template_id:
            row = conn.execute("SELECT * FROM mail_templates WHERE id=?", (int(template_id),)).fetchone()
        elif name:
            row = conn.execute("SELECT * FROM mail_templates WHERE name=?", (str(name),)).fetchone()
        else:
            row = None
    return _template_payload(row)


def save_template(name, sender, subject, content, items, expire_days=365, style=None):
    name = str(name or "").strip()
    if not name or not str(subject or "").strip():
        return {"result": 1, "error": "name and subject are required"}
    now = now_ts()
    try:
        expire_days = int(expire_days or 365)
    except (TypeError, ValueError):
        expire_days = 365
    with connect() as conn:
        row = conn.execute("SELECT id FROM mail_templates WHERE name=?", (name,)).fetchone()
        values = (
            str(sender or "penguin_logistics"),
            str(subject),
            str(content or ""),
            json.dumps(_items(items), ensure_ascii=False, separators=(",", ":")),
            expire_days,
            json.dumps(style or {"route": 0, "banner": ""}, ensure_ascii=False),
            now,
        )
        if row:
            conn.execute(
                """UPDATE mail_templates SET sender=?,subject=?,content=?,items_json=?,expire_days=?,style_json=?,updated_at=?
                   WHERE id=?""",
                values + (row["id"],),
            )
            template_id = row["id"]
        else:
            cursor = conn.execute(
                """INSERT INTO mail_templates(name,sender,subject,content,items_json,expire_days,style_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (name,) + values[:-1] + (now, now),
            )
            template_id = cursor.lastrowid
    return {"result": 0, "id": template_id, "name": name}


def delete_template(template_id):
    with connect() as conn:
        conn.execute("DELETE FROM mail_templates WHERE id=?", (int(template_id),))
    return {"result": 0}


def _next_mail_id(conn):
    row = conn.execute("SELECT MAX(mail_id) AS m FROM mail_instances").fetchone()
    return max(FIRST_MAIL_ID, int(row["m"] or 0) + 1)


def send_mail(uid, template_id=None, template_name=None, sender=None, subject=None, content=None,
              items=None, expire_days=365):
    with _lock:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account = conn.execute("SELECT * FROM accounts WHERE uid=?", (int(uid),)).fetchone()
            if not account:
                return {"result": 1, "error": "account not found"}
            tmpl = None
            if template_id or template_name:
                if template_id:
                    tmpl = conn.execute("SELECT * FROM mail_templates WHERE id=?", (int(template_id),)).fetchone()
                else:
                    tmpl = conn.execute("SELECT * FROM mail_templates WHERE name=?", (str(template_name),)).fetchone()
                if not tmpl:
                    return {"result": 1, "error": "mail template not found"}
            tmpl = dict(tmpl) if tmpl else {}
            mail_items = _items(items if items is not None else tmpl.get("items_json"))
            raw_days = expire_days if expire_days is not None else tmpl.get("expire_days")
            try:
                days = int(365 if raw_days in (None, "") else raw_days)
            except (TypeError, ValueError):
                days = 365
            now = now_ts()
            mail_id = _next_mail_id(conn)
            style = _json(tmpl.get("style_json"), {"route": 0, "banner": ""})
            conn.execute(
                """INSERT INTO mail_instances(mail_id,account_id,uid,template_id,template_name,sender,subject,content,
                   items_json,create_at,expire_at,receive_at,state,has_item,removed,type,survey_mail_id,collection_id,style_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,0,?,0,1,NULL,NULL,?)""",
                (
                    mail_id,
                    account["id"],
                    account["uid"],
                    tmpl.get("id"),
                    tmpl.get("name"),
                    str(sender or tmpl.get("sender") or "penguin_logistics"),
                    str(subject or tmpl.get("subject") or "来自罗德岛的邮件"),
                    str(content if content is not None else tmpl.get("content") or ""),
                    json.dumps(mail_items, ensure_ascii=False, separators=(",", ":")),
                    now,
                    -1 if days <= 0 else now + days * 86400,
                    1 if mail_items else 0,
                    json.dumps(style, ensure_ascii=False),
                ),
            )
    return {"result": 0, "mailId": mail_id, "uid": int(uid), "items": mail_items}


def send_bulk(uids, **kwargs):
    sent = []
    errors = []
    for uid in uids:
        result = send_mail(uid, **kwargs)
        if result.get("result") == 0:
            sent.append(int(uid))
        else:
            errors.append({"uid": int(uid), "error": result.get("error", "send failed")})
    return {"result": 0 if sent else 1, "sent": sent, "errors": errors}


def list_for_uid(uid, include_removed=False):
    with connect() as conn:
        sql = "SELECT * FROM mail_instances WHERE uid=?"
        if not include_removed:
            sql += " AND removed=0"
        sql += " ORDER BY create_at DESC, mail_id DESC"
        rows = conn.execute(sql, (int(uid),)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["items"] = _items(item.get("items_json"))
        item["style"] = _json(item.get("style_json"), {})
        result.append(item)
    return result


def _active(row, now=None):
    now = int(now or now_ts())
    return bool(row) and not int(row["removed"] or 0) and (int(row["expire_at"]) < 0 or int(row["expire_at"]) >= now)


def meta_list(uid):
    result = []
    for row in list_for_uid(uid):
        if not _active(row):
            continue
        result.append(
            {
                "mailId": int(row["mail_id"]),
                "surveyMailId": row.get("survey_mail_id"),
                "createTime": int(row["create_at"]),
                "state": int(row["state"]),
                "hasItem": bool(row["has_item"]),
                "type": int(row["type"]),
            }
        )
    return result


def _mail_item(row):
    return {
        "mailId": int(row["mail_id"]),
        "type": int(row["type"]),
        "uid": str(row["uid"]),
        "items": _items(row.get("items_json")),
        "from": row["sender"],
        "subject": row["subject"],
        "content": row["content"],
        "createAt": int(row["create_at"]),
        "expireAt": int(row["expire_at"]),
        "receiveAt": int(row.get("receive_at") or 0),
        "state": int(row["state"]),
        "style": _json(row.get("style_json"), {"route": 0, "banner": ""}),
        "collectionId": row.get("collection_id"),
    }


def list_mailbox(uid, mail_ids=None):
    wanted = {int(value) for value in (mail_ids or []) if str(value).lstrip("-").isdigit()}
    rows = []
    for row in list_for_uid(uid):
        if not _active(row):
            continue
        if wanted and int(row["mail_id"]) not in wanted:
            continue
        rows.append(_mail_item(row))
    return rows


def _unread_count(conn, uid):
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM mail_instances WHERE uid=? AND removed=0 AND state=0",
        (int(uid),),
    ).fetchone()
    return int(row["c"] or 0)


def unread_count(uid):
    with connect() as conn:
        return _unread_count(conn, uid)


def _receive_one(conn, user, uid, mail_id):
    row = conn.execute(
        "SELECT * FROM mail_instances WHERE uid=? AND mail_id=? AND removed=0",
        (int(uid), int(mail_id)),
    ).fetchone()
    if not row or not _active(row):
        return None, "mail not found or expired"
    if int(row["state"] or 0) == 1:
        return None, "mail already received"
    items = _items(row["items_json"])
    granted, delta = apply_items(user, items, source="mail")
    conn.execute(
        "UPDATE mail_instances SET state=1,receive_at=? WHERE uid=? AND mail_id=?",
        (now_ts(), int(uid), int(mail_id)),
    )
    return {"items": granted, "delta": delta, "mailId": int(mail_id)}, None


def receive_mail(account, mail_id):
    with _lock:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_row = conn.execute("SELECT * FROM accounts WHERE uid=?", (account["uid"],)).fetchone()
            if not account_row:
                return {"result": 2, "error": "account not found"}
            user = _json(account_row["user_json"], {})
            result, error = _receive_one(conn, user, account_row["uid"], mail_id)
            if error:
                return {"result": 1, "error": error}
            user.setdefault("pushFlags", {})["hasGifts"] = 1 if _unread_count(conn, account_row["uid"]) else 0
            result["delta"]["modified"]["pushFlags"] = user["pushFlags"]
            conn.execute(
                "UPDATE accounts SET user_json=? WHERE id=?",
                (json.dumps(user, ensure_ascii=False, separators=(",", ":")), account_row["id"]),
            )
            return {
                "result": 0,
                "items": result["items"],
                "playerDataDelta": result["delta"],
            }


def receive_all(account, mail_ids=None):
    with _lock:
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account_row = conn.execute("SELECT * FROM accounts WHERE uid=?", (account["uid"],)).fetchone()
            if not account_row:
                return {"result": 2, "error": "account not found"}
            user = _json(account_row["user_json"], {})
            wanted = [int(value) for value in (mail_ids or []) if str(value).lstrip("-").isdigit()]
            if not wanted:
                rows = conn.execute(
                    "SELECT * FROM mail_instances WHERE uid=? AND removed=0 ORDER BY create_at DESC, mail_id DESC",
                    (account_row["uid"],),
                ).fetchall()
                wanted = [int(row["mail_id"]) for row in rows if _active(row)]
            items = []
            delta = empty_delta()
            for mail_id in wanted:
                result, error = _receive_one(conn, user, account_row["uid"], mail_id)
                if error:
                    continue
                items.extend(result["items"])
                for key, value in result["delta"]["modified"].items():
                    delta["modified"][key] = value
            user.setdefault("pushFlags", {})["hasGifts"] = 1 if _unread_count(conn, account_row["uid"]) else 0
            delta["modified"]["pushFlags"] = user["pushFlags"]
            conn.execute(
                "UPDATE accounts SET user_json=? WHERE id=?",
                (json.dumps(user, ensure_ascii=False, separators=(",", ":")), account_row["id"]),
            )
            return {"result": 0, "items": items, "playerDataDelta": delta}


def remove_mail(uid, mail_ids, received_only=False):
    ids = [int(value) for value in (mail_ids or []) if str(value).lstrip("-").isdigit()]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    with connect() as conn:
        sql = f"UPDATE mail_instances SET removed=1 WHERE uid=? AND mail_id IN ({placeholders})"
        if received_only:
            sql += " AND state=1"
        cursor = conn.execute(sql, (int(uid), *ids))
    return cursor.rowcount


def delete_instance(mail_id):
    with connect() as conn:
        cursor = conn.execute("DELETE FROM mail_instances WHERE mail_id=?", (int(mail_id),))
    return cursor.rowcount
