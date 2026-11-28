import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import protocol_catalog  # noqa: E402
from app.main import app  # noqa: E402


def _registered_paths():
    paths = set()
    for route in app.router.routes:
        path = getattr(route, "path", None)
        if path:
            paths.add(path)
        for inner in getattr(getattr(route, "original_router", None), "routes", []) or []:
            paths.add(inner.path)
    return paths


def test_every_catalog_route_is_explicitly_registered():
    catalog = protocol_catalog.load()
    missing = [row["path"] for row in catalog.get("routes", []) if row["path"] not in _registered_paths()]
    assert missing == []


def test_feature_route_returns_domain_state(client, login):
    body = client.post(
        f"{client.base_url}/rlv2/activeRecruitTicket",
        headers=login["headers"],
        json={"id": "t_1"},
        timeout=15,
    ).json()
    assert body["result"] == 0
    assert "rlv2" in body["playerDataDelta"]["modified"]
    player = body["playerDataDelta"]["modified"]["rlv2"]["current"]["player"]
    assert "t_1" in player["tickets"]


def test_feature_routes_have_no_wildcard_handler():
    catalog = protocol_catalog.load()
    feature_paths = {row["path"] for row in catalog.get("routes", []) if row.get("kind") == "feature"}
    endpoints = {}
    for route in app.router.routes:
        for inner in getattr(getattr(route, "original_router", None), "routes", []) or [route]:
            path = getattr(inner, "path", "")
            if path in feature_paths:
                endpoints[path] = getattr(inner, "name", "")
    assert set(endpoints) == feature_paths
    assert not any(name.startswith("feature:") for name in endpoints.values())


def _post(client, login, path, body=None):
    response = client.post(
        f"{client.base_url}{path}",
        headers=login["headers"],
        json=body or {},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_campaign_v2_flow_uses_campaign_table(client, login):
    start = _post(client, login, "/campaignV2/battleStart", {"stageId": "camp_01"})
    assert start["result"] == 0
    assert start["battleId"]
    finish = _post(client, login, "/campaignV2/battleFinish", {"stageId": "camp_01", "killCnt": 400})
    assert finish["result"] == 0
    assert finish["currentFeeAfter"] >= 400
    reward = _post(client, login, "/campaignV2/getBreakReward", {"stageId": "camp_01", "indexList": [0]})
    assert reward["result"] == 0
    assert reward["items"]


def test_tower_flow_persists_layer_and_reward(client, login):
    created = _post(client, login, "/tower/createGame", {"tower": "tower_tr_01"})
    assert created["result"] == 0
    started = _post(client, login, "/tower/battleStart", {"stageId": "lt_tr_01"})
    assert started["result"] == 0
    finished = _post(client, login, "/tower/battleFinish", {"stageId": "lt_tr_01", "completeState": 3})
    assert finished["result"] == 0
    reward = _post(client, login, "/tower/layerReward", {"tower": "tower_tr_01", "layers": [1]})
    assert reward["result"] == 0


def test_sandbox_v2_create_build_and_next_day(client, login):
    created = _post(client, login, "/sandboxPerm/sandboxV2/createGame", {"topic": "sandbox_1"})
    assert created["result"] == 0
    built = _post(client, login, "/sandboxPerm/sandboxV2/build", {"building": {"seed": 1}})
    assert built["result"] == 0
    advanced = _post(client, login, "/sandboxPerm/sandboxV2/nextDay")
    assert advanced["result"] == 0
    state = advanced["playerDataDelta"]["modified"]["sandboxPerm"]["template"]["SANDBOX_V2"]["sandbox_1"]
    assert state["game"]["day"] >= 2
    assert state["base"]["building"] == {"seed": 1}


def test_activity_roguelike_create_returns_run_state(client, login):
    created = _post(client, login, "/activity/roguelike/createGame", {"theme": "rogue_5", "mode": "EASY"})
    assert created["result"] == 0
    run = created["playerDataDelta"]["modified"]["activity"]["runs"]["roguelike"]
    assert run["state"] == "RUNNING"
