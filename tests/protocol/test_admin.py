import os


def test_admin_login_page(client):
    response = client.get(f"{client.base_url}/admin/login", timeout=10)
    assert response.status_code == 200
    assert "管理" in response.text or "登录" in response.text


def test_admin_login_and_development_page(client):
    login = client.post(
        f"{client.base_url}/admin/login",
        data={
            "username": os.environ.get("ARK_ADMIN_USER", "sdars"),
            "password": os.environ.get("ARK_ADMIN_PASSWORD", "123456"),
        },
        allow_redirects=False,
        timeout=10,
    )
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
