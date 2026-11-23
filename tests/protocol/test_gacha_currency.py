import copy
import json
import os
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import gacha_service, grants  # noqa: E402


DB_PATH = Path(os.environ.get("ARK_DB", ROOT / "backend" / "data" / "arknights.sqlite3"))


def _template():
    return json.loads((ROOT / "backend" / "data" / "player_template.json").read_text(encoding="utf-8"))


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


def _sync_user(client, login):
    body = client.post(
        f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=20
    ).json()
    assert body["result"] == 0, body
    return body["user"]


def test_newbee_pool_uses_discounted_price():
    meta = gacha_service.pool_meta("BOOT_0_1_3")
    assert meta["singlePrice"] == 380
    assert meta["tenPrice"] == 3800
    assert gacha_service.pool_meta("NORM_79_0_4")["tenPrice"] == 6000


def test_newbee_ten_pull_charges_3800_and_updates_remaining_count():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 3800
    user["gacha"]["newbee"] = {"cnt": 21, "poolId": "BOOT_0_1_3", "openFlag": 1}
    result = gacha_service.draw(user, "BOOT_0_1_3", count=10, use_tkt=0)
    assert result["result"] == 0, result
    assert user["status"]["diamondShard"] == 0
    assert user["gacha"]["newbee"]["cnt"] == 11
    assert len(result["gachaResultList"]) == 10


def test_newbee_pool_rejects_draw_beyond_remaining_count():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 3800
    user["gacha"]["newbee"] = {"cnt": 5, "poolId": "BOOT_0_1_3", "openFlag": 1}
    result = gacha_service.draw(user, "BOOT_0_1_3", count=10, use_tkt=0)
    assert result["result"] == 400
    assert user["status"]["diamondShard"] == 3800
    assert user["gacha"]["newbee"]["cnt"] == 5


def test_ten_pull_permit_is_consumed_without_diamond_shard():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 0
    user["status"]["tenGachaTicket"] = 1
    result = gacha_service.draw(user, "NORM_79_0_4", count=10, use_tkt=2)
    assert result["result"] == 0, result
    assert user["status"]["tenGachaTicket"] == 0
    assert user["status"]["diamondShard"] == 0


def test_ten_pull_accepts_ten_single_tickets_from_client():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 0
    user["status"]["gachaTicket"] = 10
    result = gacha_service.draw(user, "NORM_79_0_4", count=10, use_tkt=5)
    assert result["result"] == 0, result
    assert user["status"]["gachaTicket"] == 0
    assert user["status"]["diamondShard"] == 0
    assert len(result["gachaResultList"]) == 10


def test_ten_pull_accepts_combine_item_list_and_consumes_exact_counts():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 0
    user["status"]["tenGachaTicket"] = 1
    result = gacha_service.draw(
        user,
        "NORM_79_0_4",
        count=10,
        use_tkt=9,
        item_list=[{"id": "7004", "count": 1}],
    )
    assert result["result"] == 0, result
    assert user["status"]["tenGachaTicket"] == 0
    assert user["status"]["diamondShard"] == 0


def test_ten_pull_combines_single_tickets_and_ory():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 3000
    user["status"]["gachaTicket"] = 5
    result = gacha_service.draw(
        user,
        "NORM_79_0_4",
        count=10,
        use_tkt=9,
        item_list=[{"id": "7003", "count": 5}, {"id": "4003", "count": 3000}],
    )
    assert result["result"] == 0, result
    assert user["status"]["gachaTicket"] == 0
    assert user["status"]["diamondShard"] == 0


def test_classic_ten_ticket_and_classic_single_tickets_are_supported():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 0
    user["status"]["classicTenGachaTicket"] = 1
    result = gacha_service.draw(user, "CLASSIC_37_0_1", count=10, use_tkt=7)
    assert result["result"] == 0, result
    assert user["status"]["classicTenGachaTicket"] == 0

    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 0
    user["status"]["classicGachaTicket"] = 10
    result = gacha_service.draw(user, "CLASSIC_37_0_1", count=10, use_tkt=8)
    assert result["result"] == 0, result
    assert user["status"]["classicGachaTicket"] == 0


def test_combine_item_list_is_atomic_and_never_falls_back_to_ory():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 60000
    user["status"]["tenGachaTicket"] = 1
    result = gacha_service.draw(
        user,
        "NORM_79_0_4",
        count=10,
        use_tkt=9,
        item_list=[{"id": "does_not_exist", "count": 1}, {"id": "7004", "count": 1}],
    )
    assert result["result"] == 5771
    assert user["status"]["tenGachaTicket"] == 1
    assert user["status"]["diamondShard"] == 60000


def test_ticket_request_without_ticket_does_not_charge_diamond_shard():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 60000
    user["status"]["classicTenGachaTicket"] = 0
    result = gacha_service.draw(user, "CLASSIC_37_0_1", count=10, use_tkt=7)
    assert result["result"] == 5771
    assert user["status"]["diamondShard"] == 60000


def test_single_ticket_item_id_is_consumed():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 0
    user["status"]["gachaTicket"] = 1
    result = gacha_service.draw(user, "NORM_79_0_4", count=1, use_tkt=1, item_id="7003")
    assert result["result"] == 0, result
    assert user["status"]["gachaTicket"] == 0


def test_ten_pull_closes_the_one_time_five_star_guarantee_counter():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 12000
    user["gacha"]["normal"] = {
        "NORM_79_0_4": {"rarity": 4, "maxCnt": 10, "avail": True, "cnt": 0}
    }
    result = gacha_service.draw(user, "NORM_79_0_4", count=10, use_tkt=0)
    assert result["result"] == 0, result
    node = user["gacha"]["normal"]["NORM_79_0_4"]
    assert node["avail"] is False
    assert node["cnt"] == 0
    assert node["maxCnt"] == 10


def test_gacha_state_repairs_negative_guarantee_counter():
    user = copy.deepcopy(_template())
    user["gacha"]["normal"] = {
        "NORM_79_0_3": {"rarity": 4, "maxCnt": 10, "avail": True, "cnt": 20}
    }
    gacha_service.ensure_gacha_state(user)
    node = user["gacha"]["normal"]["NORM_79_0_3"]
    assert node["cnt"] == 0
    assert node["avail"] is False


def test_new_double_pool_is_loaded_and_has_client_detail():
    meta = gacha_service.pool_meta("DOUBLE_79_0_5")
    assert meta["gachaRuleType"] == "DOUBLE"
    assert meta["singlePrice"] == 600
    assert meta["tenPrice"] == 6000
    detail = gacha_service.pool_detail("DOUBLE_79_0_5")
    assert detail and detail["hasRateUp"] is True
    assert any(row["rarityRank"] == 5 and len(row["charIdList"]) >= 4 for row in detail["detailInfo"]["availCharInfo"]["perAvailList"])


def test_double_pool_ten_pull_uses_combined_ory_and_writes_double_state():
    user = copy.deepcopy(_template())
    user["status"]["diamondShard"] = 6080
    result = gacha_service.draw(
        user,
        "DOUBLE_79_0_5",
        count=10,
        use_tkt=9,
        item_list=[{"id": "4003", "count": 6000}],
    )
    assert result["result"] == 0, result
    assert user["status"]["diamondShard"] == 80
    assert len(result["gachaResultList"]) == 10
    node = user["gacha"]["double"]["DOUBLE_79_0_5"]
    assert node["showCnt"] == 10
    assert node["hitCharState"] == 0
    assert "DOUBLE_79_0_5" not in user["gacha"]["normal"]


def test_choose_pool_up_records_both_double_selections():
    user = copy.deepcopy(_template())
    result = gacha_service.choose_pool_up(
        user,
        "DOUBLE_79_0_5",
        {"0": ["char_4027_heyak"], "1": ["char_4138_narant"]},
    )
    assert result["result"] == 0, result
    node = user["gacha"]["double"]["DOUBLE_79_0_5"]
    assert node["hitCharIds"] == {"1": "char_4027_heyak", "2": "char_4138_narant"}
    assert node["hitCharId"] == "char_4027_heyak"


def test_single_pull_does_not_use_the_ten_pull_five_star_guarantee():
    pool_id = "TEST_SINGLE_GUARANTEE"
    gacha_service.save_pool(
        pool_id,
        {
            "gachaPoolName": "单抽保底回归测试",
            "rate6": 0,
            "rate5": 0,
            "rate4": 0,
            "rate3": 1,
            "guarantee5Avail": 1,
            "guarantee5Count": 10,
            "singlePrice": 600,
        },
        create=True,
    )
    try:
        user = copy.deepcopy(_template())
        user["status"]["diamondShard"] = 600
        result = gacha_service.draw(user, pool_id, count=1, use_tkt=0)
        assert result["result"] == 0, result
        assert result["charGet"]["rarity"] == 2
    finally:
        gacha_service.delete_pool(pool_id)


def test_gacha_state_uses_rule_specific_client_buckets():
    user = copy.deepcopy(_template())
    gacha_service._pool_state(user, "LIMITED_79_0_1", 10, results=[])
    assert "LIMITED_79_0_1" in user["gacha"]["limit"]
    assert user["gacha"]["limit"]["LIMITED_79_0_1"]["poolCnt"] == 10
    assert "LIMITED_79_0_1" not in user["gacha"]["normal"]

    gacha_service._pool_state(user, "SINGLE_78_0_1", 1, results=[])
    assert "SINGLE_78_0_1" in user["gacha"]["single"]
    assert "SINGLE_78_0_1" not in user["gacha"]["normal"]

    gacha_service._pool_state(user, "FESCLASSIC_79_0_1", 1, results=[])
    assert "FESCLASSIC_79_0_1" in user["gacha"]["fesClassic"]
    assert "FESCLASSIC_79_0_1" not in user["gacha"]["normal"]


def test_gacha_state_migrates_legacy_attain_character_counters():
    user = copy.deepcopy(_template())
    user["gacha"]["attain"] = {"char_289_gyuki": 5, "ATTAIN_57_0_4": {"attain6Count": 2}}
    gacha_service.ensure_gacha_state(user)
    assert "char_289_gyuki" not in user["gacha"]["attain"]
    assert user["gacha"]["attain"]["ATTAIN_57_0_4"] == {"attain6Count": 2}
    assert user["exgachadata"]["attainChars"]["char_289_gyuki"] == 5


def test_exchange_diamond_shard_uses_account_platform(client, login, test_account):
    _admin_login(client)
    uid = int(test_account["uid"])
    response = client.post(
        f"{client.base_url}/admin/accounts/{uid}/status",
        data={"androidDiamond": "100", "iosDiamond": "50", "diamondShard": "0"},
        allow_redirects=False,
        timeout=15,
    )
    assert response.status_code == 303
    exchanged = client.post(
        f"{client.base_url}/user/exchangeDiamondShard",
        headers=login["headers"],
        json={"count": 10},
        timeout=15,
    ).json()
    assert exchanged["result"] == 0, exchanged
    user = _sync_user(client, login)
    assert user["status"]["androidDiamond"] == 90
    assert user["status"]["iosDiamond"] == 50
    assert user["status"]["diamondShard"] == 1800

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE accounts SET platform='ios' WHERE uid=?", (uid,))
    try:
        exchanged = client.post(
            f"{client.base_url}/user/exchangeDiamondShard",
            headers=login["headers"],
            json={"count": 5},
            timeout=15,
        ).json()
        assert exchanged["result"] == 0, exchanged
        user = _sync_user(client, login)
        assert user["status"]["androidDiamond"] == 90
        assert user["status"]["iosDiamond"] == 45
        assert user["status"]["diamondShard"] == 2700
    finally:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE accounts SET platform='android' WHERE uid=?", (uid,))

    too_many = client.post(
        f"{client.base_url}/user/exchangeDiamondShard",
        headers=login["headers"],
        json={"count": 10000},
        timeout=15,
    ).json()
    assert too_many["result"] == 1
    assert _sync_user(client, login)["status"]["diamondShard"] == 2700


def test_diamond_status_key_prefers_explicit_platform():
    assert grants.diamond_status_key({"localPlatform": "ios"}) == "iosDiamond"
    assert grants.diamond_status_key({"localPlatform": "android"}, "ios") == "iosDiamond"
