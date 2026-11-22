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


def test_dump_summary_and_search(client):
    assert admin_login(client).status_code == 303
    response = client.get(
        f"{client.base_url}/admin/protocol/dump/api",
        params={"q": "LoginRequest", "limit": 20},
        timeout=60,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    summary = body["summary"]
    assert summary["exists"] is True
    assert summary["type_count"] == 50548
    assert summary["image_count"] == 89
    assert summary["method_count"] > 300000
    assert summary["stringliteral"]["count"] > 30000
    assert summary["script"]["methods"] > 500000
    assert body["total"] >= 1
    assert any("LoginRequest" in row["type"] for row in body["results"])


def test_dump_type_detail(client):
    assert admin_login(client).status_code == 303
    response = client.get(
        f"{client.base_url}/admin/protocol/dump/type",
        params={"name": "XDSDK.LoginRequest"},
        timeout=60,
    )
    assert response.status_code == 200, response.text
    assert "public string account" in response.text
    assert "PlatformKey platform" in response.text
    assert "TypeDefIndex: 207" in response.text


def test_dump_page(client):
    assert admin_login(client).status_code == 303
    page = client.get(f"{client.base_url}/admin/protocol/dump", timeout=60)
    assert page.status_code == 200
    assert "dump.cs 全量索引" in page.text
    assert "stringliteral.json" in page.text
    assert "script.json" in page.text
