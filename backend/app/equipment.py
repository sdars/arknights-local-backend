"""Server-side state for operator modules (equipment).

The client builds the module panel from the persisted ``equip`` map.
An Elite-2 operator with an empty map falls back to the
"Elite Phase 2 unlocks the module system" placeholder, because the
client has no slot to render and no ``currentEquip`` to select.
Keep the server shape aligned with an official save.
"""

from . import game_data


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _equip_rows(char_id, tmpl_id=None):
    table = game_data.excel("uniequip_table.json") or {}
    equip_dict = table.get("equipDict") or {}
    if not isinstance(equip_dict, dict):
        return []
    wanted_char = str(char_id or "")
    wanted_tmpl = str(tmpl_id or "")
    rows = []
    for equip_id, meta in equip_dict.items():
        if not isinstance(meta, dict):
            continue
        if str(meta.get("charId") or "") != wanted_char:
            continue
        if str(meta.get("tmplId") or "") != wanted_tmpl:
            continue
        rows.append((str(equip_id), meta))
    rows.sort(key=lambda item: (_int(item[1].get("charEquipOrder"), 999), item[0]))
    return rows


def build_equip_slots(char_id, tmpl_id=None, phase=0):
    """Return the full ``equip`` map for one character/module template."""
    rows = _equip_rows(char_id, tmpl_id)
    slots = {}
    phase = _int(phase, 0)
    for index, (equip_id, meta) in enumerate(rows):
        initial = str(meta.get("type") or "") == "INITIAL"
        slots[equip_id] = {
            "hide": 0 if phase >= 2 else 1,
            "level": 1,
            "locked": 0 if (initial or index == 0) else 1,
        }
    return slots


def default_equip_id(char_id, tmpl_id=None):
    for equip_id, meta in _equip_rows(char_id, tmpl_id):
        if str(meta.get("type") or "") == "INITIAL":
            return equip_id
    return ""


def list_modules(char, char_id=None, tmpl_id=None):
    """Return the module definitions joined with a character's saved state."""
    if not isinstance(char, dict):
        return []
    char_id = str(char_id or char.get("charId") or "")
    if not char_id:
        return []
    phase = _int(char.get("evolvePhase"), 0)
    equip = char.get("equip") if isinstance(char.get("equip"), dict) else {}
    current = str(char.get("currentEquip") or "")
    rows = []
    for index, (equip_id, meta) in enumerate(_equip_rows(char_id, tmpl_id)):
        slot = equip.get(equip_id) if isinstance(equip.get(equip_id), dict) else {}
        initial = str(meta.get("type") or "") == "INITIAL"
        missions = meta.get("missionList") or []
        unlock_phase = str(meta.get("unlockEvolvePhase") or "")
        unlock_level = _int(meta.get("unlockLevel"), 0)
        mission_count = len(missions) if isinstance(missions, list) else 0
        max_level = 1 if initial else 3
        if initial:
            condition = "初始"
        else:
            parts = []
            phase_label = {"PHASE_0": "精英0", "PHASE_1": "精英1", "PHASE_2": "精英2"}.get(unlock_phase, unlock_phase)
            if phase_label:
                parts.append(phase_label)
            if unlock_level:
                parts.append(f"Lv.{unlock_level}")
            if mission_count:
                parts.append(f"任务 {mission_count}")
            condition = " · ".join(parts) or "无"
        rows.append(
            {
                "id": equip_id,
                "name": str(meta.get("uniEquipName") or equip_id),
                "type": str(meta.get("type") or ""),
                "type_name": str(meta.get("typeName1") or meta.get("type") or ""),
                "level": max(1, min(max_level, _int(slot.get("level"), 1))),
                "max_level": max_level,
                "locked": _int(slot.get("locked"), 0 if (initial or index == 0) else 1),
                "hide": _int(slot.get("hide"), 0 if phase >= 2 else 1),
                "equipped": current == equip_id,
                "unlock_phase": unlock_phase,
                "unlock_level": unlock_level,
                "mission_count": mission_count,
                "condition": condition,
            }
        )
    return rows


def ensure_char_equipment(char, char_id=None, tmpl_id=None, phase=None):
    """Create/repair module slots on a character (or a template patch).

    Existing module levels and lock states are preserved.  Missing slots are
    added, visibility follows Elite phase, and an Elite-2 character receives a
    valid default ``currentEquip`` when none was selected yet.
    """
    if not isinstance(char, dict):
        return False
    char_id = str(char_id or char.get("charId") or "")
    if not char_id:
        return False
    rows = _equip_rows(char_id, tmpl_id)
    if not rows:
        return False
    phase = _int(char.get("evolvePhase") if phase is None else phase, 0)
    equip = char.get("equip")
    changed = False
    if not isinstance(equip, dict):
        equip = {}
        char["equip"] = equip
        changed = True
    for index, (equip_id, meta) in enumerate(rows):
        slot = equip.get(equip_id)
        if not isinstance(slot, dict):
            slot = {}
            equip[equip_id] = slot
            changed = True
        initial = str(meta.get("type") or "") == "INITIAL"
        if "level" not in slot:
            slot["level"] = 1
            changed = True
        else:
            max_level = 1 if initial else 3
            level = _int(slot.get("level"), 1)
            bounded = max(1, min(max_level, level))
            if level != bounded:
                slot["level"] = bounded
                changed = True
        if "locked" not in slot:
            slot["locked"] = 0 if (initial or index == 0) else 1
            changed = True
        wanted_hide = 0 if phase >= 2 else 1
        if "hide" not in slot:
            slot["hide"] = wanted_hide
            changed = True
        elif _int(slot.get("hide"), 1 if phase < 2 else 0) != wanted_hide:
            slot["hide"] = wanted_hide
            changed = True
    if phase >= 2:
        current = str(char.get("currentEquip") or "")
        if not current or current not in equip:
            wanted = default_equip_id(char_id, tmpl_id)
            if wanted:
                char["currentEquip"] = wanted
                changed = True
    return changed


def ensure_user_equipment(user):
    """Repair module state for every character (and template patch)."""
    if not isinstance(user, dict):
        return False
    changed = False
    equipment_root = user.get("equipment")
    if not isinstance(equipment_root, dict):
        user["equipment"] = {"missions": {}}
        changed = True
    elif not isinstance(equipment_root.get("missions"), dict):
        equipment_root["missions"] = {}
        changed = True
    chars = ((user.get("troop") or {}).get("chars") or {})
    for char in chars.values():
        if not isinstance(char, dict):
            continue
        char_id = str(char.get("charId") or "")
        if not char_id:
            continue
        phase = _int(char.get("evolvePhase"), 0)
        current_tmpl = str(char.get("currentTmpl") or "")
        if not current_tmpl:
            changed = ensure_char_equipment(char, char_id, None, phase) or changed
        tmpl = char.get("tmpl")
        if isinstance(tmpl, dict):
            for tmpl_id, patch in tmpl.items():
                if isinstance(patch, dict):
                    changed = ensure_char_equipment(patch, char_id, str(tmpl_id), phase) or changed
    return changed
