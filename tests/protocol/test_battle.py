import time


def test_battle_start(client, login):
    body = client.post(
        f"{client.base_url}/quest/battleStart",
        headers=login["headers"],
        json={
            "isRetro": 0,
            "pry": 0,
            "battleType": 0,
            "multiple": None,
            "extra": None,
            "usePracticeTicket": 0,
            "stageId": "guide_01",
            "squad": None,
            "assistFriend": None,
            "isReplay": 0,
            "startTs": int(time.time()),
        },
        timeout=15,
    ).json()
    assert body["result"] == 0
    assert body.get("battleId")
    assert body["playerDataDelta"]["modified"]["dungeon"]["stages"]["guide_01"]["stageId"] == "guide_01"


def test_battle_finish(client, login):
    started = client.post(
        f"{client.base_url}/quest/battleStart", headers=login["headers"], json={"stageId": "guide_01"}, timeout=15
    ).json()
    assert started["result"] == 0
    body = client.post(
        f"{client.base_url}/quest/battleFinish",
        headers=login["headers"],
        json={"battleId": started["battleId"], "result": 0, "stats": {}},
        timeout=15,
    ).json()
    assert body["result"] == 0


def test_unknown_route_returns_empty_delta(client, login):
    body = client.post(
        f"{client.base_url}/protocol/test/route-not-in-capture", headers=login["headers"], json={}, timeout=10
    ).json()
    assert body["result"] == 0
    assert body["playerDataDelta"] == {"deleted": {}, "modified": {}}
