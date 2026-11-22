SHOP_ENDPOINTS = [
    "/shop/getCashGoodList",
    "/shop/getLowGoodList",
    "/shop/getHighGoodList",
    "/shop/getExtraGoodList",
    "/shop/getGPGoodList",
    "/shop/getLMTGSGoodList",
    "/shop/getEPGSGoodList",
    "/shop/getRepGoodList",
    "/shop/getSocialGoodList",
    "/shop/getClassicGoodList",
    "/shop/getSkinGoodList",
]


def test_sync_status_returns_result_object(client, login):
    response = client.post(
        f"{client.base_url}/account/syncStatus",
        headers=login["headers"],
        json={"modules": 0, "params": {}},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body["result"], dict)
    assert isinstance(body["ts"], int)
    assert body["playerDataDelta"] == {"deleted": {}, "modified": {}}


def test_cash_good_list_has_goods(client, login):
    response = client.post(
        f"{client.base_url}/shop/getCashGoodList",
        headers=login["headers"],
        json={},
        timeout=15,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["goodList"]) == 6
    assert body["goodList"][0]["goodId"] == "CS_1_r7"
    assert body["playerDataDelta"] == {"deleted": {}, "modified": {}}


def test_shop_lists_are_populated(client, login):
    for path in SHOP_ENDPOINTS:
        response = client.post(
            f"{client.base_url}{path}",
            headers=login["headers"],
            json={},
            timeout=20,
        )
        assert response.status_code == 200, (path, response.text)
        body = response.json()
        assert "playerDataDelta" in body, path
        assert any(key in body for key in ("goodList", "goods", "groups", "weeklyGroup")), path
