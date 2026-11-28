import json
import os
import sqlite3
import time
import base64
import io
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("ARK_DB", ROOT / "backend" / "data" / "arknights.sqlite3"))


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


def sync_user(client, login):
    response = client.post(f"{client.base_url}/account/syncData", headers=login["headers"], json={}, timeout=15)
    body = response.json()
    assert body["result"] == 0, body
    return body["user"]


def test_account_console_status_inventory_and_assets(client, test_account, login):
    admin_login(client)
    uid = test_account["uid"]
    page = client.get(f"{client.base_url}/admin/accounts/{uid}", timeout=20)
    assert page.status_code == 200
    for anchor in ("profile", "currency", "inventory", "assets", "operators", "squads", "mail", "advanced"):
        assert f'id="{anchor}"' in page.text
    assert 'name="action" value="potential"' in page.text
    assert "保存潜能" in page.text
    assert 'name="action" value="skill"' in page.text
    assert "保存技能等级" in page.text
    assert 'name="action" value="module"' in page.text
    assert "模组管理" in page.text

    before = sync_user(client, login)["status"]["mainStageProgress"]
    status = {
        "level": "42",
        "exp": "1234",
        "gold": "765432",
        "diamondShard": "8888",
        "androidDiamond": "66",
        "iosDiamond": "77",
        "payDiamond": "11",
        "freeDiamond": "22",
        "gachaTicket": "31",
        "tenGachaTicket": "3",
        "classicGachaTicket": "21",
        "classicTenGachaTicket": "2",
        "recruitLicense": "41",
        "instantFinishTicket": "51",
        "practiceTicket": "61",
        "hggShard": "71",
        "lggShard": "81",
        "classicShard": "91",
        "socialPoint": "101",
        "ap": "120",
        "maxAp": "200",
        "buyApRemainTimes": "9",
        "progress": "7",
        "secretary": "char_002_amiya",
        "secretarySkinId": "char_002_amiya#1",
        "avatarId": "avatar_def_01",
        "globalVoiceLan": "CN_MANDARIN",
        "resume": "admin console test",
    }
    response = client.post(
        f"{client.base_url}/admin/accounts/{uid}/status",
        data=status,
        allow_redirects=False,
        timeout=15,
    )
    assert response.status_code == 303
    user = sync_user(client, login)
    for key, value in status.items():
        if key in {
            "resume",
            "secretary",
            "secretarySkinId",
            "avatarId",
            "globalVoiceLan",
        }:
            assert user["status"][key] == value
        elif key == "progress":
            # Mainline synchronization advances tutorial progress beyond the
            # raw field value to keep the client tutorial state machine coherent.
            assert user["status"][key] >= int(value)
        else:
            assert user["status"][key] == int(value), (key, user["status"].get(key))
    # The ordinary status form must not implicitly clear or advance a mainline
    # stage just because it contains progress-related fields.
    assert user["status"]["mainStageProgress"] == before

    add = client.post(
        f"{client.base_url}/admin/accounts/{uid}/inventory",
        data={"item_id": "30012", "count": "3", "mode": "add"},
        allow_redirects=False,
        timeout=15,
    )
    assert add.status_code == 303
    user = sync_user(client, login)
    base_count = int(user["inventory"].get("30012", 0))
    assert base_count >= 3

    set_value = client.post(
        f"{client.base_url}/admin/accounts/{uid}/inventory",
        data={"item_id": "30012", "item_type": "MATERIAL", "count": "11", "mode": "set"},
        allow_redirects=False,
        timeout=15,
    )
    assert set_value.status_code == 303
    assert int(sync_user(client, login)["inventory"]["30012"]) == 11

    delete = client.post(
        f"{client.base_url}/admin/accounts/{uid}/inventory",
        data={"item_id": "30012", "item_type": "MATERIAL", "count": "0", "mode": "delete"},
        allow_redirects=False,
        timeout=15,
    )
    assert delete.status_code == 303
    assert "30012" not in sync_user(client, login)["inventory"]

    for grant_type, grant_id in (
        ("CHAR_SKIN", "char_1035_wisdel@game#9"),
        ("PLAYER_AVATAR", "avatar_special_76"),
    ):
        granted = client.post(
            f"{client.base_url}/admin/accounts/{uid}/grant",
            data={"grant_type": grant_type, "grant_id": grant_id, "grant_count": "1"},
            allow_redirects=False,
            timeout=15,
        )
        assert granted.status_code == 303
    user = sync_user(client, login)
    assert "char_1035_wisdel@game#9" in user["skin"]["characterSkins"]
    assert "avatar_special_76" in user["avatar"]["avatar_icon"]

    removed = client.post(
        f"{client.base_url}/admin/accounts/{uid}/asset/remove",
        data={"kind": "skin", "asset_id": "char_1035_wisdel@game#9"},
        allow_redirects=False,
        timeout=15,
    )
    assert removed.status_code == 303
    assert "char_1035_wisdel@game#9" not in sync_user(client, login)["skin"]["characterSkins"]

    with sqlite3.connect(DB_PATH) as conn:
        user_json = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()[0]
    persisted = json.loads(user_json)
    assert persisted["status"]["gold"] == 765432
    assert persisted["status"]["mainStageProgress"] == before


def test_account_console_operators_squads_and_protocol(client, test_account, login):
    admin_login(client)
    uid = test_account["uid"]
    response = client.post(
        f"{client.base_url}/admin/accounts/{uid}/operator-add",
        data={"char_id": "char_003_kalts", "count": "2"},
        allow_redirects=False,
        timeout=20,
    )
    assert response.status_code == 303
    user = sync_user(client, login)
    added = [
        (inst_id, char)
        for inst_id, char in user["troop"]["chars"].items()
        if char.get("charId") == "char_003_kalts"
    ]
    assert added
    inst_id = added[0][0]

    potential = client.post(
        f"{client.base_url}/admin/accounts/{uid}/operator/{inst_id}",
        data={"action": "potential", "potentialRank": "4"},
        allow_redirects=False,
        timeout=20,
    )
    assert potential.status_code == 303
    assert sync_user(client, login)["troop"]["chars"][inst_id]["potentialRank"] == 4

    skill = client.post(
        f"{client.base_url}/admin/accounts/{uid}/operator/{inst_id}",
        data={"action": "skill", "mainSkillLvl": "6"},
        allow_redirects=False,
        timeout=20,
    )
    assert skill.status_code == 303
    assert sync_user(client, login)["troop"]["chars"][inst_id]["mainSkillLvl"] == 6

    update = client.post(
        f"{client.base_url}/admin/accounts/{uid}/operator/{inst_id}",
        data={
            "level": "80",
            "evolvePhase": "2",
            "potentialRank": "5",
            "mainSkillLvl": "7",
            "favorPoint": "200",
            "skin": "char_003_kalts#1",
            "voiceLan": "CN_MANDARIN",
        },
        allow_redirects=False,
        timeout=20,
    )
    assert update.status_code == 303
    user = sync_user(client, login)
    char = user["troop"]["chars"][inst_id]
    assert char["level"] == 80
    assert char["evolvePhase"] == 2
    assert char["potentialRank"] == 5
    assert char["mainSkillLvl"] == 7
    assert char["favorPoint"] == 200
    # Elite 2 must also expose the module slots and select a base module.
    assert char["equip"]["uniequip_001_kalts"]["hide"] == 0
    assert char["equip"]["uniequip_001_kalts"]["locked"] == 0
    assert char["equip"]["uniequip_002_kalts"]["hide"] == 0
    assert char["equip"]["uniequip_002_kalts"]["locked"] == 1
    assert char["currentEquip"] == "uniequip_001_kalts"

    # Login sync repairs a legacy character whose equipment map was empty.
    with sqlite3.connect(DB_PATH) as conn:
        raw = conn.execute("SELECT user_json FROM accounts WHERE uid=?", (int(uid),)).fetchone()[0]
        legacy = json.loads(raw)
        legacy_char = legacy["troop"]["chars"][inst_id]
        legacy_char["equip"] = {}
        legacy_char["currentEquip"] = None
        conn.execute(
            "UPDATE accounts SET user_json=? WHERE uid=?",
            (json.dumps(legacy, ensure_ascii=False, separators=(",", ":")), int(uid)),
        )
    user = sync_user(client, login)
    char = user["troop"]["chars"][inst_id]
    assert char["currentEquip"] == "uniequip_001_kalts"
    assert char["equip"]["uniequip_001_kalts"]["hide"] == 0
    assert char["equip"]["uniequip_002_kalts"]["hide"] == 0
    assert char["equip"]["uniequip_001_kalts"]["locked"] == 0
    assert char["equip"]["uniequip_002_kalts"]["locked"] == 1

    page = client.get(f"{client.base_url}/admin/accounts/{uid}?module={inst_id}", timeout=20)
    assert page.status_code == 200
    assert 'name="action" value="module"' in page.text
    assert "uniequip_001_kalts" in page.text
    assert "全部解锁并满级" in page.text

    module_save = client.post(
        f"{client.base_url}/admin/accounts/{uid}/operator/{inst_id}",
        data={
            "action": "module",
            "mode": "save",
            "currentEquip": "uniequip_002_kalts",
            "unlocked_uniequip_001_kalts": "1",
            "level_uniequip_001_kalts": "3",
            "level_uniequip_002_kalts": "3",
            "level_uniequip_003_kalts": "2",
        },
        allow_redirects=False,
        timeout=20,
    )
    assert module_save.status_code == 303
    char = sync_user(client, login)["troop"]["chars"][inst_id]
    assert char["currentEquip"] == "uniequip_002_kalts"
    assert char["equip"]["uniequip_001_kalts"]["locked"] == 0
    assert char["equip"]["uniequip_001_kalts"]["level"] == 1
    assert char["equip"]["uniequip_002_kalts"]["locked"] == 0
    assert char["equip"]["uniequip_002_kalts"]["level"] == 3
    assert char["equip"]["uniequip_003_kalts"]["locked"] == 1
    assert char["equip"]["uniequip_003_kalts"]["level"] == 2

    module_unlock = client.post(
        f"{client.base_url}/admin/accounts/{uid}/operator/{inst_id}",
        data={"action": "module", "mode": "unlock_all"},
        allow_redirects=False,
        timeout=20,
    )
    assert module_unlock.status_code == 303
    char = sync_user(client, login)["troop"]["chars"][inst_id]
    assert all(slot["locked"] == 0 for slot in char["equip"].values())
    assert char["equip"]["uniequip_001_kalts"]["level"] == 1
    assert char["equip"]["uniequip_002_kalts"]["level"] == 3

    module_max = client.post(
        f"{client.base_url}/admin/accounts/{uid}/operator/{inst_id}",
        data={"action": "module", "mode": "max_all"},
        allow_redirects=False,
        timeout=20,
    )
    assert module_max.status_code == 303
    char = sync_user(client, login)["troop"]["chars"][inst_id]
    for module_id, slot in char["equip"].items():
        assert slot["locked"] == 0
        assert slot["level"] == (1 if module_id == "uniequip_001_kalts" else 3)
    assert char["currentEquip"] == "uniequip_002_kalts"

    inst_ids = [key for key, value in user["troop"]["chars"].items() if isinstance(value, dict)]
    assert len(inst_ids) >= 2
    squad_payload = {"slot_0": inst_ids[0], "slot_1": inst_ids[1], "name": "控制台编队"}
    squad = client.post(
        f"{client.base_url}/admin/accounts/{uid}/squad/0",
        data=squad_payload,
        allow_redirects=False,
        timeout=20,
    )
    assert squad.status_code == 303
    user = sync_user(client, login)
    assert user["troop"]["squads"]["0"]["name"] == "控制台编队"
    assert user["troop"]["squads"]["0"]["slots"][0]["charInstId"] == int(inst_ids[0])
    assert user["troop"]["squads"]["0"]["slots"][1]["charInstId"] == int(inst_ids[1])
    assert all(slot is None for slot in user["troop"]["squads"]["0"]["slots"][2:])

    add_squad = client.post(
        f"{client.base_url}/admin/accounts/{uid}/squad-add",
        data={"name": "新增编队"},
        allow_redirects=False,
        timeout=15,
    )
    assert add_squad.status_code == 303
    user = sync_user(client, login)
    new_squad_id = max(user["troop"]["squads"], key=lambda value: int(value))
    assert user["troop"]["squads"][new_squad_id]["name"] == "新增编队"

    protocol = client.post(
        f"{client.base_url}/quest/squadFormation",
        headers=login["headers"],
        json={
            "squadId": "1",
            "slots": [
                {"charInstId": int(inst_ids[0]), "skillIndex": 0, "currentEquip": None},
                {"charInstId": int(inst_ids[1]), "skillIndex": -1, "currentEquip": None},
            ],
            "changeSkill": True,
        },
        timeout=15,
    ).json()
    assert protocol["result"] == 0
    assert protocol["playerDataDelta"]["modified"]["troop"]["squads"]["1"]["slots"][0]["charInstId"] == int(inst_ids[0])
    renamed = client.post(
        f"{client.base_url}/quest/changeSquadName",
        headers=login["headers"],
        json={"squadId": "1", "name": "协议编队"},
        timeout=15,
    ).json()
    assert renamed["result"] == 0
    user = sync_user(client, login)
    assert user["troop"]["squads"]["1"]["name"] == "协议编队"

    delete_squad = client.post(
        f"{client.base_url}/admin/accounts/{uid}/squad/{new_squad_id}/delete",
        allow_redirects=False,
        timeout=15,
    )
    assert delete_squad.status_code == 303
    assert new_squad_id not in sync_user(client, login)["troop"]["squads"]

    delete_char = client.post(
        f"{client.base_url}/admin/accounts/{uid}/operator/{inst_id}/delete",
        allow_redirects=False,
        timeout=15,
    )
    assert delete_char.status_code == 303
    user = sync_user(client, login)
    assert inst_id not in user["troop"]["chars"]
    for squad_data in user["troop"]["squads"].values():
        assert all(slot is None or str(slot.get("charInstId")) != inst_id for slot in squad_data.get("slots") or [])


def test_account_console_generates_battle_record(client, test_account, login):
    admin_login(client)
    uid = test_account["uid"]
    response = client.post(
        f"{client.base_url}/admin/accounts/{uid}/battle/generate",
        data={"stage_id": "0-2", "complete_state": "3", "with_replay": "1"},
        allow_redirects=False,
        timeout=30,
    )
    assert response.status_code == 303
    user = sync_user(client, login)
    stage = user["dungeon"]["stages"]["main_00-02"]
    assert stage["state"] == 3
    assert stage["completeTimes"] >= 1
    # The generator now writes a decodable proxy-command replay alongside the clear.
    assert stage["hasBattleReplay"] == 1
    assert stage["battleReplay"]
    raw = base64.b64decode(stage["battleReplay"] + "=" * (-len(stage["battleReplay"]) % 4))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert "default_entry" in archive.namelist()
        replay = json.loads(archive.read("default_entry"))
    assert replay["journal"]["metadata"]["stageId"] == "main_00-02"
    assert replay["journal"]["metadata"]["gameResult"] == 1
    assert replay["journal"]["squad"]
    with sqlite3.connect(DB_PATH) as conn:
        record = conn.execute(
            "SELECT stage_id,complete_state,result,payload_json FROM battle_records "
            "WHERE uid=? AND stage_id=? ORDER BY id DESC LIMIT 1",
            (int(uid), "main_00-02"),
        ).fetchone()
    assert record is not None
    assert record[0] == "main_00-02"
    assert record[1] == 3
    assert record[2] == "win"
    payload = json.loads(record[3])
    assert payload["_battleFinish"]["adminGenerated"] is True
