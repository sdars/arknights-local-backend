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
                raw_json TEXT
            );
            CREATE TABLE IF NOT EXISTS battle_sessions (
                battle_id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                stage_id TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                finished_at INTEGER
            );
            """
        )
        order_columns = {row["name"] for row in conn.execute("PRAGMA table_info(orders)")}
        if "granted" not in order_columns:
            conn.execute("ALTER TABLE orders ADD COLUMN granted INTEGER NOT NULL DEFAULT 0")
        if "granted_at" not in order_columns:
            conn.execute("ALTER TABLE orders ADD COLUMN granted_at INTEGER")
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

