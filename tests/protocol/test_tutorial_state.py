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


def test_terminal_pointer_targets_the_next_mainline_stage():
    user = copy.deepcopy(_template())
    result = battles.sync_mainline_progress(user, "main_00-10")
    assert result["result"] == 0
    assert result["nextStage"] == "main_00-11"
    assert user["status"]["mainStageProgress"] == "main_00-11"
    assert user["dungeon"]["stages"]["main_00-11"]["state"] == 0

    result = battles.sync_mainline_progress(user, "main_00-11")
    assert result["nextStage"] == "main_01-01"
    assert user["status"]["mainStageProgress"] == "main_01-01"


def test_mainline_sequence_covers_every_episode_without_variant_duplicates():
    sequence = battles.main_stage_sequence()
    assert len(sequence) == len(set(sequence))
    assert sequence[0] == "main_00-01"
    assert "main_00-11" in sequence
    assert sequence.index("main_00-11") == sequence.index("main_00-10") + 1
    assert sequence.index("main_01-01") == sequence.index("main_00-11") + 1
    assert "main_08-01" in sequence
    assert "st_08-06" in sequence
    assert sequence.index("main_08-01") < sequence.index("st_08-06") < sequence.index("main_09-01")
    assert any(stage.startswith("main_17-") for stage in sequence)


def test_repair_uses_previous_stage_for_terminal_pointer():
    assert battles.mainline_cleared_target("main_00-01") == ""
    assert battles.mainline_cleared_target("main_00-11") == "main_00-10"
    assert battles.mainline_cleared_target("main_01-01") == "main_00-11"
    assert battles.mainline_cleared_target("not-a-stage") == ""


def test_mainline_overview_explains_chapter_and_stage_totals():
    user = copy.deepcopy(_template())
    battles.sync_mainline_progress(user, "main_00-10")
    overview = battles.mainline_overview(user)
    assert overview["chapters"] == 18
    assert overview["stages"] >= 310
    assert overview["completed"] == 10
    assert overview["current"] == "main_00-11"
    assert overview["current_index"] == 11


def test_natural_clear_advances_terminal_pointer_and_unlocks_0_11():
    user = copy.deepcopy(_template())
    stages = user.setdefault("dungeon", {}).setdefault("stages", {})
    stages["main_00-10"] = {
        "hasBattleReplay": 0,
        "practiceTimes": 0,
        "state": 3,
        "completeTimes": 1,
        "stageId": "main_00-10",
        "startTimes": 1,
    }
    next_stage = battles.advance_mainline_after_clear(user, "main_00-10")
    assert next_stage == "main_00-11"
    assert user["status"]["mainStageProgress"] == "main_00-11"
    assert user["dungeon"]["stages"]["main_00-11"]["state"] == 0
