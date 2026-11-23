import json
import os
import sqlite3
import time

from conftest import DB_PATH


def admin_login(client):
    return client.post(
        f"{client.base_url}/admin/login",
        data={
            "username": os.environ.get("ARK_ADMIN_USER", "sdars"),
            "password": os.environ.get("ARK_ADMIN_PASSWORD", "123456"),
        },
        allow_redirects=False,
        timeout=10,
    )


def test_mail_center_uses_resource_picker_and_validates_ids(client, login, test_account):
    assert admin_login(client).status_code == 303
    page = client.get(f"{client.base_url}/admin/mail", timeout=20)
    assert page.status_code == 200
    assert "resource-picker" in page.text
    assert "附件 JSON" not in page.text

    subject = f"resource-picker-{int(time.time())}"
    sent = client.post(
        f"{client.base_url}/admin/mail/send",
        data={
            "scope": "one",
            "uid": test_account["uid"],
            "subject": subject,
            "content": "结构化资源附件测试",
            "item_id": "4001",
            "item_count": "12345",
            "expire_days": "30",
        },
        allow_redirects=False,
        timeout=20,
    )
    assert sent.status_code == 303
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT mail_id,items_json FROM mail_instances WHERE uid=? AND subject=? ORDER BY mail_id DESC LIMIT 1",
            (int(test_account["uid"]), subject),
        ).fetchone()
    assert row is not None
    assert json.loads(row[1]) == [{"id": "4001", "type": "GOLD", "count": 12345}]
    mail_id = row[0]

    skin_subject = f"resource-skin-{int(time.time())}"
    skin = client.post(
        f"{client.base_url}/admin/mail/send",
        data={
            "scope": "one",
            "uid": test_account["uid"],
            "subject": skin_subject,
            "item_id": "char_1035_wisdel@game#9",
            "item_count": "1",
        },
        allow_redirects=False,
        timeout=20,
    )
    assert skin.status_code == 303
    with sqlite3.connect(DB_PATH) as conn:
        skin_row = conn.execute(
            "SELECT mail_id,items_json FROM mail_instances WHERE uid=? AND subject=? ORDER BY mail_id DESC LIMIT 1",
            (int(test_account["uid"]), skin_subject),
        ).fetchone()
    assert skin_row is not None
    assert json.loads(skin_row[1]) == [
        {"id": "char_1035_wisdel@game#9", "type": "CHAR_SKIN", "count": 1}
    ]

    with sqlite3.connect(DB_PATH) as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM mail_instances WHERE uid=? AND subject=?",
            (int(test_account["uid"]), "invalid-resource-test"),
        ).fetchone()[0]
    invalid = client.post(
        f"{client.base_url}/admin/mail/send",
        data={
            "scope": "one",
            "uid": test_account["uid"],
            "subject": "invalid-resource-test",
            "item_id": "not_a_real_resource",
            "item_count": "1",
        },
        timeout=20,
    )
    assert invalid.status_code == 200
    assert "不存在的资源 ID" in invalid.text
    with sqlite3.connect(DB_PATH) as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM mail_instances WHERE uid=? AND subject=?",
            (int(test_account["uid"]), "invalid-resource-test"),
        ).fetchone()[0]
    assert after == before

    client.post(
        f"{client.base_url}/admin/mail/{mail_id}/delete",
        allow_redirects=False,
        timeout=20,
    )
    client.post(
        f"{client.base_url}/admin/mail/{skin_row[0]}/delete",
        allow_redirects=False,
        timeout=20,
    )


def test_resource_api_returns_names_for_skin_and_character(client, login):
    assert admin_login(client).status_code == 303
    response = client.get(
        f"{client.base_url}/admin/api/resources", params={"q": "char_1035_wisdel"}, timeout=20
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert any(item["id"] == "char_1035_wisdel" and item["name"] == "维什戴尔" for item in items)
    assert any(item["id"] == "char_1035_wisdel@game#9" for item in items)
