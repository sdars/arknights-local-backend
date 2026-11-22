import json
import time


def test_sdk_config(client):
    response = client.get(f"{client.base_url}/app/v1/config", timeout=10)
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == 0
    assert body["data"]["app"]["enableAutoLogin"] is True
    assert body["data"]["app"]["enableAuthenticate"] is False


def test_server_time(client):
    body = client.get(f"{client.base_url}/general/v1/server_time", timeout=10).json()
    assert body["status"] == 0
    assert abs(int(body["data"]["serverTime"]) - int(time.time())) < 30


def test_network_config(client):
    body = client.get(
        f"{client.base_url}/api/remote_config/1/prod/default/Android/network_config", timeout=10
    ).json()
    assert body["devsdk"] is False
    for key in ("gs", "as", "u8", "hu", "hv"):
        assert body[key]


def test_basic(client, test_account):
    body = client.get(
        f"{client.base_url}/user/info/v1/basic",
        params={"token": test_account["token"]},
        timeout=10,
    ).json()
    assert body["status"] == 0
    assert body["data"]["identityNum"] == test_account["uid"]


def test_grant(client, test_account):
    body = client.post(
        f"{client.base_url}/user/oauth2/v2/grant",
        json={
            "token": test_account["token"],
            "type": 0,
            "appCode": "7318def77669979d",
            "deviceToken": test_account["device_token"],
        },
        timeout=10,
    ).json()
    assert body["status"] == 0
    assert body["data"]["uid"] == test_account["uid"]
    assert body["data"]["code"] == test_account["token"]


def test_u8_get_token(client, test_account):
    body = client.post(
        f"{client.base_url}/u8/user/v1/getToken",
        json={"extension": json.dumps({"code": test_account["token"]})},
        timeout=10,
    ).json()
    assert body["result"] == 0
    assert str(body["uid"]) == test_account["uid"]
    assert body["token"] == test_account["token"]


def test_product_list(client):
    body = client.post(f"{client.base_url}/u8/pay/getAllProductList", json={}, timeout=15).json()
    assert isinstance(body["productList"], list)
    assert body["productList"]
