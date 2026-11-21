import json
import time
from pathlib import Path

from .config import PLAYER_TEMPLATE, SERVER_NAME
from .db import connect, now_ts
from .security import new_token


_template_cache = None


def player_template():
    global _template_cache
    if _template_cache is None:
        raw = json.loads(Path(PLAYER_TEMPLATE).read_text(encoding="utf-8"))
        _template_cache = raw.get("user", raw)
    return json.loads(json.dumps(_template_cache))


def next_uid(conn):
    row = conn.execute("SELECT MAX(uid) AS m FROM accounts").fetchone()
    return 10000000 if not row or not row["m"] else int(row["m"]) + 1


def create_account(phone, token=None, nickname="博士"):
    token = token or new_token("lab_")
    with connect() as conn:
        exists = conn.execute("SELECT * FROM accounts WHERE phone=? OR token=?", (phone, token)).fetchone()
        if exists:
            return dict(exists), False
        uid = next_uid(conn)
        user = player_template()
        status = user.setdefault("status", {})
        ts = now_ts()
        status["uid"] = str(uid)
        status["nickName"] = nickname
        status["nickNumber"] = "0"
        status["registerTs"] = ts
        status["lastApAddTime"] = ts
        status["lastOnlineTs"] = ts
        status["lastRefreshTs"] = ts
        status["serverName"] = SERVER_NAME
        conn.execute(
            "INSERT INTO accounts(uid,phone,token,nickname,created_at,user_json) VALUES(?,?,?,?,?,?)",
            (uid, phone, token, nickname, ts, json.dumps(user, ensure_ascii=False, separators=(",", ":"))),
        )
        row = conn.execute("SELECT * FROM accounts WHERE uid=?", (uid,)).fetchone()
        return dict(row), True


def account_by_token(token):
    with connect() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE token=?", (token,)).fetchone()
        return dict(row) if row else None


def account_by_uid(uid):
    with connect() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE uid=?", (uid,)).fetchone()
        return dict(row) if row else None


def account_by_phone(phone):
    with connect() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE phone=?", (phone,)).fetchone()
        return dict(row) if row else None


def player_data(account):
    return json.loads(account["user_json"])


def save_player(account, user):
    with connect() as conn:
        conn.execute(
            "UPDATE accounts SET user_json=?, nickname=? WHERE uid=?",
            (json.dumps(user, ensure_ascii=False, separators=(",", ":")), user.get("status", {}).get("nickName", account["nickname"]), account["uid"]),
        )


def touch_login(account, device_id=None, ip=None):
    ts = now_ts()
    with connect() as conn:
        conn.execute("UPDATE accounts SET last_login_at=? WHERE uid=?", (ts, account["uid"]))
        conn.execute(
            "INSERT INTO sessions(id,account_id,device_id,ip,created_at,last_seen) VALUES(?,?,?,?,?,?)",
            (new_token("sess_"), account["id"], device_id, ip, ts, ts),
        )


def account_stats():
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()["c"]
        online = conn.execute(
            "SELECT COUNT(DISTINCT account_id) AS c FROM sessions WHERE last_seen > ?", (now_ts() - 300,)
        ).fetchone()["c"]
        banned = conn.execute("SELECT COUNT(*) AS c FROM accounts WHERE banned=1").fetchone()["c"]
        return {"total": total, "online": online, "banned": banned}


def list_accounts():
    with connect() as conn:
        return conn.execute("SELECT * FROM accounts ORDER BY uid ASC").fetchall()

