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
    assert search == {"players": [], "friendStatusList": [], "resultIdList": []}

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
