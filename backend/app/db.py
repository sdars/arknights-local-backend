import json
import sqlite3
import time
from contextlib import contextmanager

from .config import DB_PATH, ADMIN_USER, ADMIN_PASSWORD
from .security import hash_password


def now_ts():
    return int(time.time())


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid INTEGER UNIQUE NOT NULL,
                phone TEXT UNIQUE NOT NULL,
                token TEXT UNIQUE NOT NULL,
                nickname TEXT NOT NULL DEFAULT '博士',
                banned INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                last_login_at INTEGER,
                user_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                device_id TEXT,
                ip TEXT,
                created_at INTEGER NOT NULL,
                last_seen INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS request_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                status INTEGER NOT NULL,
                uid TEXT,
                ip TEXT,
                ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                uid INTEGER NOT NULL,
                token TEXT NOT NULL,
                store_id INTEGER,
                good_id TEXT,
                product_name TEXT,
                amount INTEGER NOT NULL DEFAULT 0,
                status INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                paid_at INTEGER,
                granted INTEGER NOT NULL DEFAULT 0,
                granted_at INTEGER,
                granted_items_json TEXT,
                raw_json TEXT
            );
            CREATE TABLE IF NOT EXISTS vouchers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                code TEXT NOT NULL UNIQUE,
                account_id INTEGER,
                account_uid INTEGER,
                scope_json TEXT NOT NULL DEFAULT '{}',
                max_amount INTEGER NOT NULL DEFAULT 0,
                uses_total INTEGER NOT NULL DEFAULT 1,
                uses_remaining INTEGER NOT NULL DEFAULT 1,
                start_at INTEGER NOT NULL DEFAULT 0,
                end_at INTEGER NOT NULL DEFAULT 0,
                status INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS battle_sessions (
                battle_id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                stage_id TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                finished_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS mail_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                sender TEXT NOT NULL DEFAULT 'penguin_logistics',
                subject TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                items_json TEXT NOT NULL DEFAULT '[]',
                expire_days INTEGER NOT NULL DEFAULT 365,
                style_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mail_instances (
                mail_id INTEGER PRIMARY KEY,
                account_id INTEGER NOT NULL,
                uid INTEGER NOT NULL,
                template_id INTEGER,
                template_name TEXT,
                sender TEXT NOT NULL DEFAULT 'penguin_logistics',
                subject TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                items_json TEXT NOT NULL DEFAULT '[]',
                create_at INTEGER NOT NULL,
                expire_at INTEGER NOT NULL DEFAULT -1,
                receive_at INTEGER,
                state INTEGER NOT NULL DEFAULT 0,
                has_item INTEGER NOT NULL DEFAULT 0,
                removed INTEGER NOT NULL DEFAULT 0,
                type INTEGER NOT NULL DEFAULT 1,
                survey_mail_id TEXT,
                collection_id TEXT,
                style_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_mail_instances_uid
                ON mail_instances(uid, removed, create_at DESC);
            """
        )
        order_columns = {row["name"] for row in conn.execute("PRAGMA table_info(orders)")}
        if "granted" not in order_columns:
            conn.execute("ALTER TABLE orders ADD COLUMN granted INTEGER NOT NULL DEFAULT 0")
        if "granted_at" not in order_columns:
            conn.execute("ALTER TABLE orders ADD COLUMN granted_at INTEGER")
        if "granted_items_json" not in order_columns:
            conn.execute("ALTER TABLE orders ADD COLUMN granted_items_json TEXT")
        if "platform" not in order_columns:
            conn.execute("ALTER TABLE orders ADD COLUMN platform TEXT")
        for column, ddl in (
            ("revoked", "ALTER TABLE orders ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0"),
            ("revoked_at", "ALTER TABLE orders ADD COLUMN revoked_at INTEGER"),
            ("revoked_items_json", "ALTER TABLE orders ADD COLUMN revoked_items_json TEXT"),
            ("revoke_debt_json", "ALTER TABLE orders ADD COLUMN revoke_debt_json TEXT"),
            ("voucher_id", "ALTER TABLE orders ADD COLUMN voucher_id INTEGER"),
            ("admin_note", "ALTER TABLE orders ADD COLUMN admin_note TEXT"),
            ("origin", "ALTER TABLE orders ADD COLUMN origin TEXT NOT NULL DEFAULT 'client'"),
            ("created_by", "ALTER TABLE orders ADD COLUMN created_by TEXT"),
        ):
            if column not in order_columns:
                conn.execute(ddl)
        account_columns = {row["name"] for row in conn.execute("PRAGMA table_info(accounts)")}
        if "platform" not in account_columns:
            conn.execute("ALTER TABLE accounts ADD COLUMN platform TEXT NOT NULL DEFAULT 'android'")
        conn.execute("UPDATE accounts SET platform='android' WHERE platform IS NULL OR platform=''")
        conn.execute(
            """UPDATE orders SET platform=(
                   SELECT platform FROM accounts WHERE accounts.id=orders.account_id
               )
               WHERE platform IS NULL OR platform=''"""
        )
        # Older builds credited DIAMOND to both stores.  Repair the obvious
        # double-write on Android accounts while preserving any account that
        # already carries a genuinely different iOS balance.
        for account_row in conn.execute("SELECT id,user_json,platform FROM accounts").fetchall():
            try:
                user = json.loads(account_row["user_json"])
            except Exception:
                continue
            status = user.get("status") or {}
            android = int(status.get("androidDiamond") or 0)
            ios = int(status.get("iosDiamond") or 0)
            changed = False
            if not user.get("localPlatform"):
                user["localPlatform"] = str(account_row["platform"] or "android")
                changed = True
            if str(account_row["platform"] or "") == "android" and ios == android and ios > 0:
                status["iosDiamond"] = 0
                user["status"] = status
                changed = True
            if changed:
                conn.execute(
                    "UPDATE accounts SET user_json=? WHERE id=?",
                    (json.dumps(user, ensure_ascii=False, separators=(",", ":")), account_row["id"]),
                )
        row = conn.execute("SELECT id FROM admin_users WHERE username=?", (ADMIN_USER,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO admin_users(username,password_hash,created_at) VALUES(?,?,?)",
                (ADMIN_USER, hash_password(ADMIN_PASSWORD), now_ts()),
            )


def get_setting(key, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def log_request(method, path, status, uid, ip, ms):
    with connect() as conn:
        conn.execute(
            "INSERT INTO request_logs(ts,method,path,status,uid,ip,ms) VALUES(?,?,?,?,?,?,?)",
            (now_ts(), method, path, status, uid, ip, ms),
        )
        conn.execute(
            "DELETE FROM request_logs WHERE id NOT IN (SELECT id FROM request_logs ORDER BY id DESC LIMIT 2000)"
        )


def recent_logs(limit=200):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM request_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

