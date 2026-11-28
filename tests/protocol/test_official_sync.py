import json
import os
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("ARK_DB", ROOT / "backend" / "data" / "arknights.sqlite3"))


def admin_login(client):
    response = client.post(
        f"{client.base_url}/admin/login",
        data={
            "username": os.environ.get("ARK_ADMIN_USER", "sdars"),
            "password": os.environ.get("ARK_ADMIN_PASSWORD", "123456"),
        },
        allow_redirects=False,
        timeout=10,
    )
    assert response.status_code == 303


def read_settings():
    with sqlite3.connect(DB_PATH) as conn:
        return dict(conn.execute("SELECT key,value FROM settings").fetchall())


def restore_settings(original):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM settings")
        conn.executemany("INSERT INTO settings(key,value) VALUES(?,?)", original.items())


class OfficialHandler(BaseHTTPRequestHandler):
    server_version = "OfficialSyncTest/1.0"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        state = self.server.state
        with state["lock"]:
            state["requests"] += 1
            mode = state["mode"]
            payload = dict(state["payload"])
        if mode == "error":
            body = b"upstream unavailable"
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if mode == "invalid":
            body = json.dumps({"clientVersion": "9.9.9-invalid"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        etag = state["etag"]
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def official_server():
    state = {
        "lock": threading.Lock(),
        "mode": "ok",
        "requests": 0,
        "etag": '"sync-test-v1"',
        "payload": {"clientVersion": "2.7.99-sync", "resVersion": "res-sync-2026"},
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), OfficialHandler)
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/version", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _save_sync_form(client, url, op="sync", **overrides):
    data = {
        "official_sync_enabled": "1",
        "official_sync_url": url,
        "official_sync_interval_minutes": "60",
        "official_sync_client": "1",
        "official_sync_res": "1",
        "op": op,
    }
    data.update(overrides)
    return client.post(
        f"{client.base_url}/admin/settings/official-sync",
        data=data,
        allow_redirects=False,
        timeout=30,
    )


def test_manual_sync_updates_all_version_endpoints_and_keeps_last_good_on_error(
    client, official_server
):
    url, state = official_server
    admin_login(client)
    original = read_settings()
    try:
        response = _save_sync_form(client, url)
        assert response.status_code == 303, response.text

        config = client.get(
            f"{client.base_url}/config/prod/official/Android/version", timeout=10
        ).json()
        assert config == {"resVersion": "res-sync-2026", "clientVersion": "2.7.99-sync"}
        assert client.get(f"{client.base_url}/version", timeout=10).json()["version"] == "2.7.99-sync"
        assert client.get(f"{client.base_url}/version", timeout=10).json()["resVersion"] == "res-sync-2026"
        legacy = client.post(f"{client.base_url}/admin/getVersion", timeout=10).json()
        assert legacy["clientVersion"] == "2.7.99-sync"
        assert legacy["ResVersion"] == "res-sync-2026"

        status = client.get(
            f"{client.base_url}/admin/settings/official-sync/status", timeout=10
        ).json()
        assert status["last_status"] == "updated"
        assert status["official_client_version"] == "2.7.99-sync"
        assert status["official_res_version"] == "res-sync-2026"
        assert status["last_success"]
        assert status["last_error"] == ""

        state["mode"] = "error"
        response = _save_sync_form(client, url)
        assert response.status_code == 303
        status = client.get(
            f"{client.base_url}/admin/settings/official-sync/status", timeout=10
        ).json()
        assert status["last_status"] == "error"
        assert "HTTP 500" in status["last_error"]
        assert client.get(
            f"{client.base_url}/config/prod/official/Android/version", timeout=10
        ).json() == {"resVersion": "res-sync-2026", "clientVersion": "2.7.99-sync"}

        state["mode"] = "ok"
        response = _save_sync_form(client, url)
        assert response.status_code == 303
        status = client.get(
            f"{client.base_url}/admin/settings/official-sync/status", timeout=10
        ).json()
        assert status["last_status"] == "not-modified"
        assert status["last_error"] == ""
        assert client.get(
            f"{client.base_url}/config/prod/official/Android/version", timeout=10
        ).json() == {"resVersion": "res-sync-2026", "clientVersion": "2.7.99-sync"}

        # A 304 must still reconcile values that were changed locally after the
        # last full response; otherwise the "automatic" sync would silently
        # leave a pinned version in place forever.
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE settings SET value='2.7.90-local' WHERE key='client_version'")
            conn.execute("UPDATE settings SET value='res-local-old' WHERE key='res_version'")
        response = _save_sync_form(client, url)
        assert response.status_code == 303
        status = client.get(
            f"{client.base_url}/admin/settings/official-sync/status", timeout=10
        ).json()
        assert status["last_status"] == "updated"
        assert client.get(
            f"{client.base_url}/config/prod/official/Android/version", timeout=10
        ).json() == {"resVersion": "res-sync-2026", "clientVersion": "2.7.99-sync"}

        page = client.get(f"{client.base_url}/admin/settings", timeout=20)
        assert page.status_code == 200
        assert "官方版本自动同步" in page.text
        assert "2.7.99-sync" in page.text
        assert "res-sync-2026" in page.text
        assert "已更新" in page.text
    finally:
        restore_settings(original)


def test_auto_sync_respects_disabled_and_rejects_incomplete_payload(client, official_server):
    url, state = official_server
    admin_login(client)
    original = read_settings()
    try:
        _save_sync_form(client, url, op="save", official_sync_enabled="0")
        before = state["requests"]
        sys.path.insert(0, str(ROOT / "backend"))
        from app import official_sync, runtime_settings

        result = official_sync.sync_once("auto")
        assert result["status"] == "disabled"
        assert state["requests"] == before

        _save_sync_form(client, url, op="save", official_sync_enabled="1", official_sync_client="1", official_sync_res="1")
        state["mode"] = "invalid"
        current = runtime_settings.values()
        result = official_sync.sync_once("manual")
        assert result["status"] == "error"
        after = runtime_settings.values()
        assert after["client_version"] == current["client_version"]
        assert after["res_version"] == current["res_version"]
    finally:
        restore_settings(original)

