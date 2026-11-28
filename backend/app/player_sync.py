"""Persist the last player state delivered to the game client.

The server needs a cursor to distinguish a client-poll request (nothing new)
from a state change made by the console, mail worker, payment worker, or battle
settlement.  Without it the only way to expose console changes is to echo the
whole account on every sync, which causes the client's spurious resync UI.

The snapshot is a delivery cursor, not a second source of truth.  The account
row remains authoritative; the cursor is updated only when a game response is
being returned (``services.save_player``).  Admin and background service writes
deliberately bypass it so their changes are picked up by the next poll.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import connect, now_ts


def load_snapshot(account_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT snapshot_json FROM player_sync_snapshots WHERE account_id=?",
            (int(account_id),),
        ).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row["snapshot_json"])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def store_snapshot(conn: sqlite3.Connection, account_id: int, user: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO player_sync_snapshots(account_id, snapshot_json, updated_at)
        VALUES(?,?,?)
        ON CONFLICT(account_id) DO UPDATE SET
            snapshot_json=excluded.snapshot_json,
            updated_at=excluded.updated_at
        """,
        (
            int(account_id),
            json.dumps(user, ensure_ascii=False, separators=(",", ":")),
            now_ts(),
        ),
    )


def reset_snapshot(account_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM player_sync_snapshots WHERE account_id=?", (int(account_id),))
