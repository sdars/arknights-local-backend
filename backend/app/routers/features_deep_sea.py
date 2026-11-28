"""Interlocking competition / deep-sea exploration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..db import now_ts
from ..feature_common import (
    apply_items,
    as_dict,
    as_int,
    auth_error,
    body_id,
    call,
    ensure_dict,
    fail,
    finish,
    first_value,
    unique_append,
)


router = APIRouter()


def _deep(call_obj):
    return ensure_dict(call_obj.user, "deepSea")


@router.post("/deepSea/node")
async def deep_sea_node(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    node_id = body_id(call_obj.body, "nodeId", "id")
    if not node_id:
        return fail("nodeId required")
    nodes = ensure_dict(_deep(call_obj), "nodes")
    state = as_int(first_value(call_obj.body, "state", "status", default=1), 1)
    nodes[node_id] = state
    return finish(call_obj, "deepSea")


@router.post("/deepSea/place")
async def deep_sea_place(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    place_id = body_id(call_obj.body, "placeId", "id")
    if not place_id:
        return fail("placeId required")
    ensure_dict(_deep(call_obj), "places")[place_id] = 1
    return finish(call_obj, "deepSea")


@router.post("/deepSea/story")
async def deep_sea_story(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    story_id = body_id(call_obj.body, "storyId", "id")
    if not story_id:
        return fail("storyId required")
    stories = ensure_dict(_deep(call_obj), "stories")
    stories[story_id] = as_int(call_obj.body.get("state"), 1)
    return finish(call_obj, "deepSea")


@router.post("/deepSea/event")
async def deep_sea_event(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    event_id = body_id(call_obj.body, "eventId", "id")
    if not event_id:
        return fail("eventId required")
    events = ensure_dict(_deep(call_obj), "events")
    events[event_id] = as_int(first_value(call_obj.body, "state", "result", default=1), 1)
    return finish(call_obj, "deepSea")


@router.post("/deepSea/choice")
async def deep_sea_choice(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    node_id = body_id(call_obj.body, "nodeId", "choiceId", "id")
    option = as_int(first_value(call_obj.body, "option", "index", default=0), 0)
    if not node_id:
        return fail("nodeId required")
    choices = ensure_dict(_deep(call_obj), "choices")
    selected = choices.get(node_id)
    if not isinstance(selected, list):
        selected = []
        choices[node_id] = selected
    unique_append(selected, option)
    return finish(call_obj, "deepSea")


@router.post("/deepSea/branch")
async def deep_sea_branch(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    branch_id = body_id(call_obj.body, "branchId", "id")
    if not branch_id:
        return fail("branchId required")
    log = ensure_dict(_deep(call_obj), "logs")
    log["branch"] = branch_id
    log["updatedTs"] = now_ts()
    return finish(call_obj, "deepSea")


@router.post("/deepSea/treasure")
async def deep_sea_treasure(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    treasure_id = body_id(call_obj.body, "treasureId", "id")
    if not treasure_id:
        return fail("treasureId required")
    treasures = ensure_dict(_deep(call_obj), "treasures")
    if as_int(treasures.get(treasure_id), 0) == 1:
        return fail("treasure already claimed")
    treasures[treasure_id] = 1
    items = apply_items(call_obj, [{"id": "4001", "type": "GOLD", "count": 500}], f"deep-sea:{treasure_id}")
    call_obj.extra["items"] = items
    return finish(call_obj, "deepSea")


@router.post("/deepSea/techTreeUnlock")
async def deep_sea_tech_tree_unlock(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    tree_id = body_id(call_obj.body, "techTreeId", "treeId", "id")
    tech_id = body_id(call_obj.body, "techId", "nodeId")
    if not tree_id or not tech_id:
        return fail("tech tree and tech id required")
    tree = ensure_dict(ensure_dict(_deep(call_obj), "techTrees"), tree_id)
    ensure_dict(tree, "status")[tech_id] = 2
    return finish(call_obj, "deepSea")


@router.post("/deepSea/techTreeActive")
async def deep_sea_tech_tree_active(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    tree_id = body_id(call_obj.body, "techTreeId", "treeId", "id")
    tech_id = body_id(call_obj.body, "techId", "nodeId")
    if not tree_id or not tech_id:
        return fail("tech tree and tech id required")
    tree = ensure_dict(ensure_dict(_deep(call_obj), "techTrees"), tree_id)
    ensure_dict(tree, "status")[tech_id] = 1
    tree["active"] = tech_id
    return finish(call_obj, "deepSea")
