import json
import random
import sqlite3
import sys
from pathlib import Path

from conftest import DB_PATH

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import create_account  # noqa: E402


def _user(uid):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()
    return json.loads(row[0])


def test_sort_list_info_returns_list(client, login):
    response = client.post(
        f"{client.base_url}/social/getSortListInfo",
        headers=login["headers"],
        json={"type": 0, "sortKeyList": [], "param": {}},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result"] == []
    assert body["starFriendList"] == []
    assert body["playerDataDelta"] == {"deleted": {}, "modified": {}}


def test_sort_list_info_rejects_missing_token(client):
    body = client.post(
        f"{client.base_url}/social/getSortListInfo",
        json={},
        timeout=15,
    ).json()
    assert body["result"] == 2


def test_social_empty_list_responses_have_required_fields(client, login):
    search = client.post(
        f"{client.base_url}/social/searchPlayer",
        headers=login["headers"],
        json={"idList": []},
        timeout=15,
    ).json()
    assert search["players"] == []
    assert search["friendStatusList"] == []
    assert search["resultIdList"] == []

    friends = client.post(
        f"{client.base_url}/social/getFriendList",
        headers=login["headers"],
        json={"idList": []},
        timeout=15,
    ).json()
    assert friends["friends"] == []
    assert friends["friendAlias"] == []
    assert friends["resultIdList"] == []

    requests_list = client.post(
        f"{client.base_url}/social/getFriendRequestList",
        headers=login["headers"],
        json={"idList": []},
        timeout=15,
    ).json()
    assert requests_list["requestList"] == []
    assert requests_list["resultIdList"] == []

    send_list = client.post(
        f"{client.base_url}/social/getFriendAndRequestSendList",
        headers=login["headers"],
        json={},
        timeout=15,
    ).json()
    assert send_list == {"friendsList": [], "requestSendIdList": []}


def test_assist_list_returns_support_entries(client, login, test_account):
    body = client.post(
        f"{client.base_url}/quest/getAssistList",
        headers=login["headers"],
        json={"profession": "ALL", "askRefresh": False, "currSquadId": "0"},
        timeout=15,
    ).json()
    assert body["result"] == 0
    assert body["assistList"]
    assert "starFriendAssistList" in body
    for entry in body["assistList"]:
        assert entry["uid"]
        assert entry["assistCharList"]
        for char in entry["assistCharList"]:
            assert char["charId"]
            assert "skills" in char
            assert "level" in char

    from app import game_data

    profiles = {
        str((game_data.character(char.get("charId")) or {}).get("profession") or "").upper()
        for char in ((_user(test_account["uid"]).get("troop") or {}).get("chars") or {}).values()
        if isinstance(char, dict)
    }
    for profession in sorted(prof for prof in profiles if prof):
        filtered = client.post(
            f"{client.base_url}/quest/getAssistList",
            headers=login["headers"],
            json={"profession": profession, "askRefresh": False, "currSquadId": "0"},
            timeout=15,
        ).json()
        assert filtered["result"] == 0
        assert filtered["assistList"], profession


def test_full_friend_flow(client, login, test_account):
    phone = "188" + f"{random.SystemRandom().randrange(10**8):08d}"
    target, _created = create_account(phone, nickname="好友测试")
    target_uid = str(target["uid"])
    target_headers = {"secret": target["token"], "uid": target_uid}
    try:
        target_user = _user(target_uid)
        inst_id = int(next(iter((target_user.get("troop") or {}).get("chars") or {})))
        assist = client.post(
            f"{client.base_url}/social/setAssistCharList",
            headers=target_headers,
            json={"assistCharList": [{"charInstId": inst_id, "skillIndex": 0, "currentEquip": ""}]},
            timeout=15,
        ).json()
        assert assist["result"] == 0

        search = client.post(
            f"{client.base_url}/social/getSortListInfo",
            headers=login["headers"],
            json={"type": 0, "sortKeyList": [], "param": {"nickName": "好友测试"}},
            timeout=15,
        ).json()
        assert any(row["uid"] == target_uid for row in search["result"])

        sent = client.post(
            f"{client.base_url}/social/sendFriendRequest",
            headers=login["headers"],
            json={"friendId": target_uid, "originType": 3},
            timeout=15,
        ).json()
        assert sent["result"] == 0

        incoming = client.post(
            f"{client.base_url}/social/getSortListInfo",
            headers=target_headers,
            json={"type": 2},
            timeout=15,
        ).json()
        assert any(row["uid"] == test_account["uid"] for row in incoming["result"])

        accepted = client.post(
            f"{client.base_url}/social/processFriendRequest",
            headers=target_headers,
            json={"friendId": test_account["uid"], "action": 1},
            timeout=15,
        ).json()
        assert accepted["result"] == 0

        friends = client.post(
            f"{client.base_url}/social/getSortListInfo",
            headers=login["headers"],
            json={"type": 1},
            timeout=15,
        ).json()
        assert any(row["uid"] == target_uid for row in friends["result"])

        detail = client.post(
            f"{client.base_url}/social/getFriendList",
            headers=login["headers"],
            json={"idList": [target_uid]},
            timeout=15,
        ).json()
        assert detail["friends"][0]["uid"] == target_uid
        assert detail["friends"][0]["assistCharList"][0]["charId"]

        alias = client.post(
            f"{client.base_url}/social/setFriendAlias",
            headers=login["headers"],
            json={"friendId": target_uid, "alias": "小号"},
            timeout=15,
        ).json()
        assert alias["result"] == 0
        detail = client.post(
            f"{client.base_url}/social/getFriendList",
            headers=login["headers"],
            json={"idList": [target_uid]},
            timeout=15,
        ).json()
        assert detail["friendAlias"] == ["小号"]

        starred = client.post(
            f"{client.base_url}/social/setStarFriendList",
            headers=login["headers"],
            json={"idList": [target_uid]},
            timeout=15,
        ).json()
        assert starred["result"] == 0
        friends = client.post(
            f"{client.base_url}/social/getSortListInfo",
            headers=login["headers"],
            json={"type": 1},
            timeout=15,
        ).json()
        assert target_uid in friends["starFriendList"]

        card = client.post(
            f"{client.base_url}/businessCard/getOtherPlayerNameCard",
            headers=login["headers"],
            json={"uid": target_uid, "src": "friend"},
            timeout=15,
        ).json()
        assert card["nameCard"]["uid"] == target_uid

        deleted = client.post(
            f"{client.base_url}/social/deleteFriend",
            headers=login["headers"],
            json={"friendId": target_uid},
            timeout=15,
        ).json()
        assert deleted["result"] == 0
    finally:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM accounts WHERE uid=?", (int(target_uid),))
