import json
import os
import sqlite3
import time
from pathlib import Path


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


def sync_user(client, login):
    response = client.post(f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=15)
    body = response.json()
    assert body["result"] == 0, body
    return body["user"]


def send_admin_mail(client, uid, subject, items):
    response = client.post(
        f"{client.base_url}/admin/mail/send",
        data={
            "uid": str(uid),
            "scope": "one",
            "sender": "test_mailer",
            "subject": subject,
            "content": "protocol test",
            "items": json.dumps(items, ensure_ascii=False),
            "expire_days": "2",
        },
        allow_redirects=False,
        timeout=15,
    )
    assert response.status_code == 303, response.text


def latest_mail_id(subject):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT mail_id FROM mail_instances WHERE subject=? ORDER BY mail_id DESC LIMIT 1",
            (subject,),
        ).fetchone()
    assert row, subject
    return int(row[0])


def test_admin_mail_page_and_template(client):
    admin_login(client)
    page = client.get(f"{client.base_url}/admin/mail", timeout=15)
    assert page.status_code == 200
    assert "邮件中心" in page.text
    assert "模板库" in page.text
    name = f"protocol_template_{int(time.time() * 1000)}"
    response = client.post(
        f"{client.base_url}/admin/mail/templates",
        data={
            "name": name,
            "sender": "protocol_test",
            "subject": "protocol template",
            "content": "template body",
            "items": json.dumps([{"id": "4001", "type": "GOLD", "count": 123}], ensure_ascii=False),
            "expire_days": "3",
        },
        allow_redirects=False,
        timeout=15,
    )
    assert response.status_code == 303
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id,sender,subject,items_json,expire_days FROM mail_templates WHERE name=?",
            (name,),
        ).fetchone()
    assert row
    assert row[1] == "protocol_test"
    assert json.loads(row[3]) == [{"id": "4001", "type": "GOLD", "count": 123}]
    assert row[4] == 3


def test_mail_protocol_grants_once_and_persists(client, test_account, login):
    admin_login(client)
    before = sync_user(client, login)
    before_gold = int(before["status"]["gold"])
    subject = f"mail-gold-{int(time.time() * 1000)}"
    send_admin_mail(
        client,
        test_account["uid"],
        subject,
        [{"id": "4001", "type": "GOLD", "count": 1000}],
    )
    mail_id = latest_mail_id(subject)
    headers = login["headers"]

    meta = client.post(
        f"{client.base_url}/mail/getMetaInfoList",
        headers=headers,
        json={"from": 0},
        timeout=15,
    )
    assert meta.status_code == 200
    meta_body = meta.json()
    assert isinstance(meta_body["result"], list)
    meta_item = next(item for item in meta_body["result"] if item["mailId"] == mail_id)
    assert meta_item["createTime"] > 0
    assert "createAt" not in meta_item
    assert meta_item["hasItem"] is True
    assert meta_item["type"] == 1
    assert meta_item["state"] == 0

    box = client.post(
        f"{client.base_url}/mail/listMailBox",
        headers=headers,
        json={"mailIdList": [], "sysMailIdList": [], "surveyMailIdList": []},
        timeout=15,
    )
    assert box.status_code == 200
    box_body = box.json()
    item = next(entry for entry in box_body["mailList"] if entry["mailId"] == mail_id)
    assert item["subject"] == subject
    assert item["items"] == [{"id": "4001", "type": "GOLD", "count": 1000}]
    assert item["state"] == 0
    collection = client.post(
        f"{client.base_url}/mailCollection/getList",
        headers=headers,
        json={},
        timeout=15,
    )
    assert collection.status_code == 200
    assert collection.json() == {"collections": [], "extra": []}

    unread = sync_user(client, login)
    assert unread["pushFlags"]["hasGifts"] == 1

    received = client.post(
        f"{client.base_url}/mail/receiveMail",
        headers=headers,
        json={"mailId": mail_id, "type": 1},
        timeout=15,
    )
    received_body = received.json()
    assert received.status_code == 200
    assert received_body["result"] == 0, received_body
    assert received_body["items"][0]["id"] == "4001"
    assert received_body["playerDataDelta"]["modified"]["status"]["gold"] == before_gold + 1000

    repeated = client.post(
        f"{client.base_url}/mail/receiveMail",
        headers=headers,
        json={"mailId": mail_id, "type": 1},
        timeout=15,
    ).json()
    assert repeated["result"] == 1
    assert "already received" in repeated["error"]

    after = sync_user(client, login)
    assert int(after["status"]["gold"]) == before_gold + 1000
    with sqlite3.connect(DB_PATH) as conn:
        state, receive_at = conn.execute(
            "SELECT state,receive_at FROM mail_instances WHERE mail_id=?",
            (mail_id,),
        ).fetchone()
        user_json = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(test_account["uid"]),)).fetchone()[0]
    assert state == 1
    assert receive_at > 0
    assert int(json.loads(user_json)["status"]["gold"]) == before_gold + 1000


def test_mail_receive_all_remove_and_meta_disappears(client, test_account, login):
    admin_login(client)
    headers = login["headers"]
    stamp = int(time.time() * 1000)
    subject_a = f"batch-a-{stamp}"
    subject_b = f"batch-b-{stamp}"
    send_admin_mail(client, test_account["uid"], subject_a, [{"id": "30012", "type": "MATERIAL", "count": 2}])
    send_admin_mail(client, test_account["uid"], subject_b, [{"id": "30073", "type": "MATERIAL", "count": 3}])
    ids = [latest_mail_id(subject_a), latest_mail_id(subject_b)]

    result = client.post(
        f"{client.base_url}/mail/receiveAllMail",
        headers=headers,
        json={"mailIdList": ids, "sysMailIdList": []},
        timeout=15,
    ).json()
    assert result["result"] == 0
    returned = {(item["id"], item["count"]) for item in result["items"]}
    assert ("30012", 2) in returned
    assert ("30073", 3) in returned

    removed = client.post(
        f"{client.base_url}/mail/removeAllReceivedMail",
        headers=headers,
        json={"mailIdList": ids, "sysMailIdList": []},
        timeout=15,
    ).json()
    assert removed["result"] == 0

    meta_ids = {
        item["mailId"]
        for item in client.post(
            f"{client.base_url}/mail/getMetaInfoList",
            headers=headers,
            json={"from": 0},
            timeout=15,
        ).json()["result"]
    }
    assert not (set(ids) & meta_ids)
    with sqlite3.connect(DB_PATH) as conn:
        states = conn.execute(
            "SELECT mail_id,state,removed FROM mail_instances WHERE mail_id IN (?,?) ORDER BY mail_id",
            tuple(ids),
        ).fetchall()
    assert len(states) == 2
    assert all(row[1] == 1 and row[2] == 1 for row in states)
