"""Infrastructure room queue and music endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import social as social_service
from ..feature_common import (
    as_dict,
    as_int,
    auth_error,
    body_id,
    call,
    ensure_dict,
    ensure_list,
    fail,
    finish,
    first_value,
)


router = APIRouter()


def _building(call_obj):
    return ensure_dict(call_obj.user, "building")


def _room(call_obj, slot_id: str):
    building = _building(call_obj)
    slots = ensure_dict(building, "roomSlots")
    slot = slots.get(slot_id)
    if not isinstance(slot, dict):
        return None, None
    room_id = str(slot.get("roomId") or "")
    rooms = ensure_dict(building, "rooms")
    bucket = ensure_dict(rooms, room_id)
    node = bucket.get(slot_id)
    if not isinstance(node, dict):
        node = {}
        bucket[slot_id] = node
    return slot, node


@router.post("/building/addPresetQueue")
async def building_add_preset_queue(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    slot_id = body_id(call_obj.body, "slotId", "roomSlotId")
    slot, room = _room(call_obj, slot_id)
    if not slot:
        return fail(f"unknown room slot: {slot_id}")
    queue = ensure_list(room, "presetQueue")
    raw = first_value(call_obj.body, "queue", "presets", default=[])
    if isinstance(raw, list):
        queue.extend(raw)
    else:
        queue.append(raw)
    return finish(call_obj, "building")


@router.post("/building/deletePresetQueue")
async def building_delete_preset_queue(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    slot_id = body_id(call_obj.body, "slotId", "roomSlotId")
    _slot, room = _room(call_obj, slot_id)
    if room is None:
        return fail(f"unknown room slot: {slot_id}")
    queue = ensure_list(room, "presetQueue")
    index = as_int(first_value(call_obj.body, "index", "idx", default=-1), -1)
    if 0 <= index < len(queue):
        queue.pop(index)
    elif queue:
        queue.pop()
    return finish(call_obj, "building")


@router.post("/building/editPresetQueue")
async def building_edit_preset_queue(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    slot_id = body_id(call_obj.body, "slotId", "roomSlotId")
    _slot, room = _room(call_obj, slot_id)
    if room is None:
        return fail(f"unknown room slot: {slot_id}")
    queue = ensure_list(room, "presetQueue")
    index = as_int(first_value(call_obj.body, "index", "idx", default=0), 0)
    value = call_obj.body.get("queue")
    while len(queue) <= index:
        queue.append({})
    queue[index] = value if isinstance(value, dict) else {"chars": value if isinstance(value, list) else []}
    return finish(call_obj, "building")


@router.post("/building/useOnePresetQueue")
async def building_use_preset_queue(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    slot_id = body_id(call_obj.body, "slotId", "roomSlotId")
    slot, room = _room(call_obj, slot_id)
    if not slot:
        return fail(f"unknown room slot: {slot_id}")
    queue = ensure_list(room, "presetQueue")
    index = as_int(first_value(call_obj.body, "index", "idx", default=0), 0)
    if not (0 <= index < len(queue)):
        return fail("preset queue not found")
    selected = as_dict(queue[index])
    char_ids = selected.get("chars") or selected.get("charInstIds") or []
    if isinstance(char_ids, list):
        slot["charInstIds"] = [as_int(value, -1) for value in char_ids]
    return finish(call_obj, "building")


@router.post("/building/editLockQueue")
async def building_edit_lock_queue(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    lock_pos = call_obj.body.get("lockPos")
    if not isinstance(lock_pos, dict):
        return fail("lockPos required")
    building = _building(call_obj)
    rooms = ensure_dict(building, "rooms")
    slots = ensure_dict(building, "roomSlots")
    for slot_id, positions in lock_pos.items():
        slot = slots.get(str(slot_id))
        if not isinstance(slot, dict):
            continue
        room_id = str(slot.get("roomId") or "")
        node = ensure_dict(ensure_dict(rooms, room_id), str(slot_id))
        node["lockQueue"] = positions if isinstance(positions, list) else []
    return finish(call_obj, "building")


@router.post("/building/changeBGM")
async def building_change_bgm(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    music_id = body_id(call_obj.body, "musicId", "id")
    if not music_id:
        return fail("musicId required")
    music = ensure_dict(_building(call_obj), "music")
    states = ensure_dict(music, "state")
    node = ensure_dict(states, music_id)
    if not node.get("unlock"):
        return fail("music not unlocked")
    music["selected"] = music_id
    music["inUse"] = True
    return finish(call_obj, "building")


@router.post("/building/getClueFriendList")
async def building_get_clue_friend_list(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    friend = social_service.ensure_state(call_obj.user)
    rows = []
    for uid in social_service.friend_uids(friend):
        target, target_user = social_service._target_user(uid)  # same-database projection
        if not target or not target_user:
            continue
        status = ensure_dict(target_user, "status")
        rows.append(
            {
                "uid": uid,
                "nickName": status.get("nickName") or target.get("nickname") or "博士",
                "nickNumber": status.get("nickNumber") or "0",
                "level": as_int(status.get("level"), 1),
                "avatar": as_dict(status.get("avatar")),
                "lastOnlineTime": as_int(status.get("lastOnlineTs"), 0),
                "alias": social_service.alias_of(friend, uid),
            }
        )
    return {
        "result": rows,
        "starFriendList": [],
        "playerDataDelta": {"deleted": {}, "modified": {"building": _building(call_obj), "social": call_obj.user.get("social", {})}},
    }
