import io
import os
import sqlite3
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("ARK_DB", ROOT / "backend" / "data" / "arknights.sqlite3"))
CLIENT_VERSION = os.environ.get("ARK_CLIENT_VERSION", "2.7.91")
RES_VERSION = os.environ.get("ARK_RES_VERSION", "26-11-13-06-37-23-89286612")


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


def read_settings():
    with sqlite3.connect(DB_PATH) as conn:
        return dict(conn.execute("SELECT key,value FROM settings").fetchall())


def restore_settings(original):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM settings")
        conn.executemany("INSERT INTO settings(key,value) VALUES(?,?)", original.items())


def save_settings(client, values):
    response = client.post(
        f"{client.base_url}/admin/settings",
        data=values,
        allow_redirects=False,
        timeout=15,
    )
    assert response.status_code == 303, response.text


def login_body(test_account, client_version, resource_version):
    return {
        "uid": test_account["uid"],
        "token": test_account["token"],
        "assetsVersion": resource_version,
        "clientVersion": client_version,
        "platform": 1,
        "deviceId": "update-settings-test",
        "deviceId2": "",
        "deviceId3": "",
        "networkVersion": "5",
        "udtVersion": "79.0.0",
    }


def test_force_update_maintenance_and_resource_delivery(client, test_account, tmp_path):
    admin_login(client)
    original = read_settings()
    asset_root = tmp_path / "assets"
    values = {
        "server_name": "测试泰拉",
        "announcement": "测试公告",
        "client_version": "9.9.9-test",
        "res_version": "res-test-2026",
        "force_update": "1",
        "force_launcher_update": "1",
        "force_resource_update": "1",
        "maintenance": "0",
        "maintenance_message": "测试维护",
        "asset_base_url": "http://127.0.0.1:8443/assetbundle/official",
        "asset_fallback_url": "https://example.invalid/assetbundle/official",
        "asset_root": str(asset_root),
        "update_url": "https://example.invalid/arknights.apk",
        "update_notes": "强制更新测试",
    }
    try:
        save_settings(client, values)
        version = client.get(f"{client.base_url}/config/prod/official/Android/version", timeout=10).json()
        assert version == {"resVersion": "res-test-2026", "clientVersion": "9.9.9-test"}
        remote = client.get(
            f"{client.base_url}/api/remote_config/1/prod/default/Android/remote_config",
            timeout=10,
        ).json()
        assert remote["forceUpdate"] == 1
        latest = client.get(
            f"{client.base_url}/api/game/get_latest_game_info",
            params={"platform": "Android"},
            timeout=10,
        ).json()
        assert latest["action"] == 1
        assert latest["client_version"] == "9.9.9-test"
        assert latest["update_info"]["package"]["url"] == "https://example.invalid/arknights.apk"
        assert latest["preload_info"]["res_version"] == "res-test-2026"

        stale_client = client.post(
            f"{client.base_url}/account/login",
            json=login_body(test_account, CLIENT_VERSION, "res-test-2026"),
            timeout=15,
        ).json()
        assert stale_client["result"] == 2
        stale_resource = client.post(
            f"{client.base_url}/account/login",
            json=login_body(test_account, "9.9.9-test", RES_VERSION),
            timeout=15,
        ).json()
        assert stale_resource["result"] == 4

        save_settings(
            client,
            {
                **values,
                "client_version": CLIENT_VERSION,
                "res_version": RES_VERSION,
                "force_update": "0",
                "force_launcher_update": "1",
                "force_resource_update": "0",
                "maintenance": "1",
            },
        )
        maintenance = client.post(
            f"{client.base_url}/account/login",
            json=login_body(test_account, CLIENT_VERSION, RES_VERSION),
            timeout=15,
        ).json()
        assert maintenance["result"] == 3
        assert maintenance["error"] == "测试维护"

        save_settings(
            client,
            {
                **values,
                "client_version": CLIENT_VERSION,
                "res_version": RES_VERSION,
                "force_update": "0",
                "force_launcher_update": "1",
                "force_resource_update": "0",
                "maintenance": "0",
            },
        )
        asset_root.mkdir(parents=True, exist_ok=True)
        upload = client.post(
            f"{client.base_url}/admin/settings/assets/upload",
            data={"prefix": "Android/assets/protocolhash"},
            files={"upload": ("hello.txt", b"hello-resource", "text/plain")},
            allow_redirects=False,
            timeout=20,
        )
        assert upload.status_code == 303
        local = client.get(
            f"{client.base_url}/assetbundle/official/Android/assets/protocolhash/hello.txt",
            allow_redirects=False,
            timeout=15,
        )
        assert local.status_code == 200
        assert local.content == b"hello-resource"
        head = client.head(
            f"{client.base_url}/assetbundle/official/Android/assets/protocolhash/hello.txt",
            allow_redirects=False,
            timeout=15,
        )
        assert head.status_code == 200
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("nested/manifest.json", '{"ok":true}')
            archive.writestr("../escape.txt", "blocked")
        archive_buffer.seek(0)
        zip_upload = client.post(
            f"{client.base_url}/admin/settings/assets/upload",
            data={"prefix": "Android/assets/protocolhash"},
            files={"upload": ("bundle.zip", archive_buffer.getvalue(), "application/zip")},
            allow_redirects=False,
            timeout=20,
        )
        assert zip_upload.status_code == 303
        nested = client.get(
            f"{client.base_url}/assetbundle/official/Android/assets/protocolhash/nested/manifest.json",
            allow_redirects=False,
            timeout=15,
        )
        assert nested.status_code == 200
        assert nested.content == b'{"ok":true}'
        assert not (tmp_path / "escape.txt").exists()
        assert not (asset_root / "escape.txt").exists()
        missing = client.get(
            f"{client.base_url}/assetbundle/official/Android/assets/protocolhash/missing.bin",
            allow_redirects=False,
            timeout=15,
        )
        assert missing.status_code == 302
        assert missing.headers["location"] == (
            "https://example.invalid/assetbundle/official/Android/assets/protocolhash/missing.bin"
        )
    finally:
        restore_settings(original)

    restored = client.get(f"{client.base_url}/config/prod/official/Android/version", timeout=10).json()
    assert restored["clientVersion"] == CLIENT_VERSION
    assert restored["resVersion"] == RES_VERSION
    default_update = client.get(f"{client.base_url}/api/game/get_latest_game_info", timeout=10).json()
    assert default_update["action"] == 0
