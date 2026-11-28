import json
import os
import random
import sqlite3
import time
from pathlib import Path

import pytest
import requests


ROOT = Path(__file__).resolve().parents[2]
BASE_URL = os.environ.get("ARK_TEST_BASE_URL", "http://127.0.0.1:8443").rstrip("/")
DB_PATH = Path(os.environ.get("ARK_DB", ROOT / "backend" / "data" / "arknights.sqlite3"))


@pytest.fixture(scope="session")
def client():
    session = requests.Session()
    session.headers.update({"accept": "application/json", "user-agent": "ark-protocol-test/1"})
    session.base_url = BASE_URL
    response = session.get(f"{BASE_URL}/healthz", timeout=5)
    assert response.status_code == 200, response.text
    yield session
    session.close()


def _sdk_data(response):
    body = response.json()
    assert response.status_code == 200, response.text
    assert body.get("status") == 0, body
    data = body.get("data")
    if isinstance(data, str):
        data = json.loads(data)
    return data


@pytest.fixture(scope="session")
def test_account(client):
    phone = "199" + f"{random.SystemRandom().randrange(10**8):08d}"
    response = client.post(
        f"{BASE_URL}/user/auth/v2/token_by_phone_code",
        json={"phone": phone, "code": "000000", "type": 1},
        timeout=10,
    )
    sdk = _sdk_data(response)
    account = {
        "phone": phone,
        "token": sdk["token"],
        "device_token": sdk.get("deviceToken", ""),
        "uid": "",
    }
    basic = client.get(
        f"{BASE_URL}/user/info/v1/basic",
        params={"token": account["token"]},
        timeout=10,
    ).json()
    account["uid"] = str(basic["data"]["identityNum"])
    yield account
    if DB_PATH.exists():
        with sqlite3.connect(DB_PATH) as conn:
            ids = [
                row[0]
                for row in conn.execute("SELECT id FROM accounts WHERE phone=?", (phone,)).fetchall()
            ]
            for account_id in ids:
                conn.execute("DELETE FROM battle_sessions WHERE account_id=?", (account_id,))
                conn.execute("DELETE FROM battle_records WHERE account_id=?", (account_id,))
                conn.execute("DELETE FROM orders WHERE account_id=?", (account_id,))
                conn.execute("DELETE FROM sessions WHERE account_id=?", (account_id,))
                conn.execute("DELETE FROM player_sync_snapshots WHERE account_id=?", (account_id,))
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
                if "mail_instances" in tables:
                    conn.execute("DELETE FROM mail_instances WHERE account_id=?", (account_id,))
                if "mail_templates" in tables:
                    conn.execute("DELETE FROM mail_templates WHERE name LIKE 'protocol_template_%'")
            conn.execute("DELETE FROM accounts WHERE phone=?", (phone,))


@pytest.fixture(scope="session")
def login(client, test_account):
    response = client.post(
        f"{BASE_URL}/account/login",
        json={
            "uid": test_account["uid"],
            "token": test_account["token"],
            "assetsVersion": os.environ.get("ARK_RES_VERSION", "26-11-13-06-37-23-89286612"),
            "clientVersion": os.environ.get("ARK_CLIENT_VERSION", "2.7.91"),
            "platform": 1,
            "deviceId": "protocol-test-device",
            "deviceId2": "",
            "deviceId3": "",
            "networkVersion": "5",
            "udtVersion": "79.0.0",
        },
        timeout=15,
    )
    body = response.json()
    assert response.status_code == 200, response.text
    assert body.get("result") == 0, body
    assert body.get("secret") == test_account["token"]
    headers = {
        "secret": test_account["token"],
        "uid": test_account["uid"],
        "seqnum": str(int(time.time() * 1000)),
        "x-unity-version": "2021.3.20f1",
    }
    return {"headers": headers, "body": body}
