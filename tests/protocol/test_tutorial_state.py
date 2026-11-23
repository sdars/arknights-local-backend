import copy
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import battles  # noqa: E402


def _template():
    return json.loads((ROOT / "backend" / "data" / "player_template.json").read_text(encoding="utf-8"))


def test_player_template_starts_at_0_1_without_completed_main_stages():
    user = _template()
    status = user["status"]
    assert status["mainStageProgress"] == "main_00-01"
    assert int(status.get("progress", 0)) == 0
    for stage_id, node in user["dungeon"]["stages"].items():
        if stage_id.startswith("main_00-"):
            assert int(node.get("state", 0) or 0) == 0, stage_id
            assert int(node.get("completeTimes", 0) or 0) == 0, stage_id


def test_admin_mainline_sync_keeps_every_tutorial_step_consistent():
    template = _template()
    stages = [
        "main_00-01",
        "main_00-02",
        "main_00-06",
        "main_00-10",
        "main_00-11",
        "main_01-01",
        "main_01-05",
        "main_02-08",
    ]
    for stage_id in stages:
        user = copy.deepcopy(template)
        result = battles.sync_mainline_progress(user, stage_id)
        assert result["result"] == 0, (stage_id, result)
        audit = battles.tutorial_audit(user)
        assert audit["ok"], (stage_id, audit)
        assert audit["missing"] == [], (stage_id, audit)


def test_first_stage_only_marks_its_own_training_stage():
    user = copy.deepcopy(_template())
    battles.sync_mainline_progress(user, "main_00-01")
    stages = user["dungeon"]["stages"]
    assert stages["tr_01"]["state"] == 3
    assert stages.get("tr_05", {}).get("state", 0) == 0
    assert user["status"]["progress"] < 699


def test_0_11_skips_building_chain_without_leaving_progress_behind():
    user = copy.deepcopy(_template())
    battles.sync_mainline_progress(user, "main_00-11")
    flags = user["status"]["flags"]
    for story_id in (
        "obt/guide/l0-11/0_building_init",
        "obt/guide/l0-11/1a_slot_clean",
        "obt/guide/l0-11/3d_op_trade",
        "obt/guide/bd/control_intro",
        "obt/guide/shop/1_furni",
    ):
        assert flags.get(story_id) == 1, story_id
    assert user["status"]["progress"] == 10100
