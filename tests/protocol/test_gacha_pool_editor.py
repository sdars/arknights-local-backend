import os
import random
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import admin as admin_router  # noqa: E402


def _admin_login(client):
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


def _create_pool(client, pool_id, name):
    response = client.post(
        f"{client.base_url}/admin/gacha/create",
        data={
            "pool_id": pool_id,
            "name": name,
            "base_pool": "",
            "single_price": "600",
            "ten_price": "6000",
            "rate6_percent": "100",
            "rate5_percent": "0",
            "rate4_percent": "0",
            "rate3_percent": "0",
            "guarantee5": "0",
            "guarantee5_count": "10",
            "up_chars": "",
            "up_rate_percent": "100",
        },
        allow_redirects=False,
        timeout=15,
    )
    assert response.status_code == 303


def _pool_form(avail6="银灰\n能天使", avail5="阿米娅", avail4="夜烟", avail3="芬"):
    return {
        "avail6": avail6,
        "avail5": avail5,
        "avail4": avail4,
        "avail3": avail3,
        "up6": "银灰",
        "up5": "阿米娅",
        "up6_rate_percent": "100",
        "up5_rate_percent": "50",
    }


def _detail(client, pool_id):
    response = client.post(
        f"{client.base_url}/gacha/getPoolDetail", json={"poolId": pool_id}, timeout=15
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_admin_gacha_list_puts_client_open_pools_first():
    rows = admin_router._gacha_rows()
    assert rows
    assert rows[0]["is_active"] is True
    assert rows[0]["client_visible"] is True
    assert rows[0]["client_open"] is True
    assert any(not row["client_open"] for row in rows)


def test_create_custom_pool_and_set_prize_characters_and_rates(client, login, test_account):
    _admin_login(client)
    pool_id = f"NORM_EDIT_{random.SystemRandom().randrange(100000, 999999)}"
    try:
        _create_pool(client, pool_id, "自定义奖池测试")
        rows = admin_router._gacha_rows(q=pool_id)
        assert rows and rows[0]["client_visible"] is False
        saved = client.post(
            f"{client.base_url}/admin/gacha/{pool_id}/pool",
            data=_pool_form(),
            allow_redirects=True,
            timeout=20,
        )
        assert saved.status_code == 200
        assert "奖池已保存" in saved.text

        detail = _detail(client, pool_id)
        per = {
            row["rarityRank"]: row
            for row in detail["detailInfo"]["availCharInfo"]["perAvailList"]
        }
        assert per[5]["charIdList"] == ["char_172_svrash", "char_103_angel"]
        assert per[4]["charIdList"] == ["char_002_amiya"]
        assert per[3]["charIdList"] == ["char_141_nights"]
        assert per[2]["charIdList"] == ["char_123_fang"]
        assert per[5]["totalPercent"] == 1.0
        assert per[4]["totalPercent"] == 0.0
        assert per[3]["totalPercent"] == 0.0
        assert per[2]["totalPercent"] == 0.0
        up = {
            row["rarityRank"]: row
            for row in detail["detailInfo"]["upCharInfo"]["perCharList"]
        }
        assert up[5]["charIdList"] == ["char_172_svrash"]
        assert up[5]["percent"] == 1.0
        assert up[4]["charIdList"] == ["char_002_amiya"]
        assert up[4]["percent"] == 0.5

        uid = int(test_account["uid"])
        client.post(
            f"{client.base_url}/admin/accounts/{uid}/status",
            data={"diamondShard": "60000"},
            allow_redirects=False,
            timeout=15,
        )
        drawn = client.post(
            f"{client.base_url}/gacha/tenAdvancedGacha",
            headers=login["headers"],
            json={"poolId": pool_id, "useTkt": 0, "itemList": []},
            timeout=30,
        ).json()
        assert drawn["result"] == 0, drawn
        assert len(drawn["gachaResultList"]) == 10
        assert all(row["rarity"] == 5 for row in drawn["gachaResultList"])
        assert all(row["charId"] == "char_172_svrash" for row in drawn["gachaResultList"])
    finally:
        client.post(
            f"{client.base_url}/admin/gacha/{pool_id}/delete",
            allow_redirects=False,
            timeout=15,
        )


def test_prize_pool_rejects_wrong_rarity_and_unknown_character(client, login, test_account):
    _admin_login(client)
    pool_id = f"NORM_EDIT_{random.SystemRandom().randrange(100000, 999999)}"
    try:
        _create_pool(client, pool_id, "奖池校验测试")
        wrong = client.post(
            f"{client.base_url}/admin/gacha/{pool_id}/pool",
            data=_pool_form(avail6="阿米娅"),
            allow_redirects=True,
            timeout=20,
        )
        assert wrong.status_code == 200
        assert "不能放进 6★ 奖池" in wrong.text
        unknown = client.post(
            f"{client.base_url}/admin/gacha/{pool_id}/pool",
            data=_pool_form(avail6="不存在的干员"),
            allow_redirects=True,
            timeout=20,
        )
        assert unknown.status_code == 200
        assert "找不到干员" in unknown.text
        detail = _detail(client, pool_id)
        per = {
            row["rarityRank"]: row
            for row in detail["detailInfo"]["availCharInfo"]["perAvailList"]
        }
        assert "char_002_amiya" not in per[5]["charIdList"]
    finally:
        client.post(
            f"{client.base_url}/admin/gacha/{pool_id}/delete",
            allow_redirects=False,
            timeout=15,
        )
