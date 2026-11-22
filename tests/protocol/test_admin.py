import os


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


def test_admin_login_page(client):
    response = client.get(f"{client.base_url}/admin/login", timeout=10)
    assert response.status_code == 200
    assert "管理" in response.text or "登录" in response.text


def test_admin_login_and_development_page(client):
    login = admin_login(client)
    assert login.status_code == 303
    assert login.headers["location"] == "/admin"
    page = client.get(f"{client.base_url}/admin/development", timeout=15)
    assert page.status_code == 200
    assert "静态逆向" in page.text
    assert "无头协议" in page.text
    catalog = client.get(f"{client.base_url}/admin/development/catalog.json", timeout=20)
    assert catalog.status_code == 200
    body = catalog.json()
    assert body["counts"]["routes"] >= 1
    assert body["counts"]["protocol_types"] >= 1


def test_protocol_index_and_api(client):
    assert admin_login(client).status_code == 303
    page = client.get(f"{client.base_url}/admin/protocol?q=syncData&kind=core", timeout=15)
    assert page.status_code == 200
    assert "/account/syncData" in page.text
    assert "507" in page.text
    api = client.get(f"{client.base_url}/admin/protocol/api?q=syncData&kind=core", timeout=15)
    assert api.status_code == 200
    body = api.json()
    assert body["total"] == 1
    assert body["routes"][0]["path"] == "/account/syncData"


def test_protocol_type_detail(client):
    assert admin_login(client).status_code == 303
    page = client.get(
        f"{client.base_url}/admin/protocol/type", params={"name": "Torappu.SyncDataRequest"}, timeout=15
    )
    assert page.status_code == 200
    assert "platform" in page.text
    assert "已展开" in page.text


def test_protocol_rebuild(client):
    assert admin_login(client).status_code == 303
    rebuild = client.post(f"{client.base_url}/admin/protocol/rebuild", allow_redirects=False, timeout=30)
    assert rebuild.status_code == 303
    assert "rebuilt=1" in rebuild.headers["location"]
