"""Voucher and full-potential depot endpoints."""

from __future__ import annotations

import json
import random
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Request

from .. import grants
from ..config import DATA_DIR
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
    merge_delta,
)


router = APIRouter()


@lru_cache(maxsize=1)
def _vouchers() -> dict:
    path = Path(DATA_DIR) / "voucher_detail_table.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("vouchers") or {}
    except Exception:
        return {}


def _voucher(item_id: str) -> dict:
    return as_dict(_vouchers().get(item_id))


def _consume(call_obj, item_id: str, inst_id: str, count: int) -> bool:
    if count <= 0:
        return False
    consumable = ensure_dict(call_obj.user, "consumable")
    stack = consumable.get(item_id)
    if not isinstance(stack, dict):
        return False
    inst = str(inst_id or "0")
    slot = stack.get(inst)
    if not isinstance(slot, dict):
        for key, value in stack.items():
            if isinstance(value, dict) and as_int(value.get("count"), 0) >= count:
                inst, slot = str(key), value
                break
    if not isinstance(slot, dict) or as_int(slot.get("count"), 0) < count:
        return False
    slot["count"] = as_int(slot.get("count"), 0) - count
    call_obj.touch("consumable")
    return True


def _valid_time_info():
    return {"startTs": -1, "endTs": -1}


def _allowed(voucher: dict) -> list[dict]:
    items = voucher.get("itemList")
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _selection(call_obj, voucher: dict, random_pick: bool = False) -> list[dict]:
    allowed = _allowed(voucher)
    pool = voucher.get("pool")
    if random_pick and isinstance(pool, list) and pool:
        groups = {}
        for item in pool:
            if isinstance(item, dict):
                groups.setdefault(str(item.get("groupId") or "1"), []).append(item)
        bucket = groups[random.choice(sorted(groups))]
        picked = random.choice(bucket)
        return [{"id": picked.get("itemId") or picked.get("id"), "type": picked.get("itemType") or picked.get("type") or "MATERIAL", "count": max(1, as_int(picked.get("count"), 1))}]
    choices = call_obj.body.get("choices")
    if isinstance(choices, list) and choices:
        selected = []
        for row in choices:
            row = as_dict(row)
            item_id = str(row.get("id") or "")
            if not item_id:
                continue
            match = next((item for item in allowed if str(item.get("id")) == item_id), None)
            if allowed and not match:
                return []
            selected.append(
                {
                    "id": item_id,
                    "type": (match or row).get("type") or "MATERIAL",
                    "count": max(1, as_int(row.get("count"), 1)),
                }
            )
        if selected:
            return selected
    char_id = body_id(call_obj.body, "charId", "itemId")
    if char_id and allowed:
        match = next((item for item in allowed if str(item.get("id")) == char_id), None)
        if match:
            return [{"id": char_id, "type": match.get("type") or "CHAR", "count": 1}]
    if allowed:
        first = allowed[0]
        return [{"id": first.get("id"), "type": first.get("type") or "MATERIAL", "count": max(1, as_int(first.get("count"), 1))}]
    return []


def _grant_selected(call_obj, selected: list[dict], source: str) -> list[dict]:
    before_chars = {
        str(char.get("charId")): str(key)
        for key, char in as_dict(ensure_dict(call_obj.user, "troop").get("chars")).items()
        if isinstance(char, dict)
    }
    items = apply_items(call_obj, selected, source)
    char_gets = []
    chars = as_dict(ensure_dict(call_obj.user, "troop").get("chars"))
    for item in selected:
        if str(item.get("type")).upper() not in {"CHAR", "OPERATOR"}:
            continue
        char_id = str(item.get("id") or "")
        inst_id = before_chars.get(char_id)
        if not inst_id:
            for key, char in chars.items():
                if isinstance(char, dict) and char.get("charId") == char_id:
                    inst_id = str(key)
                    break
        char_gets.append(
            {
                "charId": char_id,
                "charInstId": as_int(inst_id, 0),
                "isNew": 0 if char_id in before_chars else 1,
                "itemGet": [] if char_id not in before_chars else [{"id": f"p_{char_id}", "type": "MATERIAL", "count": 1}],
            }
        )
    return char_gets, items


@router.post("/depot/getVoucherDetail")
async def depot_get_voucher_detail(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    item_id = body_id(call_obj.body, "itemId", "id")
    meta = _voucher(item_id)
    items = _allowed(meta)
    return {
        "result": 0,
        "voucherType": meta.get("voucherType") or ("CHAR_VOUCHER" if items and items[0].get("type") == "CHAR" else "MATERIAL_VOUCHER"),
        "pickNum": max(1, as_int(meta.get("pickNum"), 1)),
        "voucherBgDec": meta.get("picId"),
        "extraDataDic": {},
        "itemList": items,
        "validTimeInfo": _valid_time_info(),
    }


@router.post("/depot/getCharGachaVoucherDetail")
async def depot_get_char_gacha_voucher_detail(request: Request):
    return await depot_get_voucher_detail(request)


@router.post("/depot/getMaterialVoucherDetail")
async def depot_get_material_voucher_detail(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    item_id = body_id(call_obj.body, "itemId", "id")
    meta = _voucher(item_id)
    return {
        "result": 0,
        "info": {
            "voucherId": item_id,
            "pickNum": max(1, as_int(meta.get("pickNum"), 1)),
            "startTime": -1,
            "endTime": -1,
            "picId": meta.get("picId"),
            "pool": meta.get("pool") or [],
        },
    }


def _use_voucher(call_obj, random_pick: bool = False):
    item_id = body_id(call_obj.body, "itemId", "id")
    inst_id = body_id(call_obj.body, "instId", "instanceId", default="0")
    count = max(1, as_int(call_obj.body.get("count") or call_obj.body.get("voucherCount"), 1))
    meta = _voucher(item_id)
    if not meta:
        return fail(f"unknown voucher: {item_id}")
    if not _consume(call_obj, item_id, inst_id, count):
        return fail("voucher not owned or insufficient")
    selected = []
    for _ in range(count):
        selected.extend(_selection(call_obj, meta, random_pick=random_pick))
    if not selected:
        return fail("no valid voucher selection")
    char_gets, items = _grant_selected(call_obj, selected, f"depot-voucher:{item_id}")
    result = finish(call_obj)
    result["itemGet"] = items
    if char_gets:
        result["items"] = items
        result["charGet"] = char_gets[0]
    return result


@router.post("/depot/useMaterialVoucher")
async def depot_use_material_voucher(request: Request):
    call_obj = await call(request)
    return _use_voucher(call_obj, random_pick=True) if call_obj else auth_error()


@router.post("/depot/useOptionVoucher")
async def depot_use_option_voucher(request: Request):
    call_obj = await call(request)
    return _use_voucher(call_obj, random_pick=False) if call_obj else auth_error()


@router.post("/depot/useCharGachaVoucher")
async def depot_use_char_gacha_voucher(request: Request):
    call_obj = await call(request)
    return _use_voucher(call_obj, random_pick=False) if call_obj else auth_error()


@router.post("/depot/voucherGacha")
async def depot_voucher_gacha(request: Request):
    call_obj = await call(request)
    return _use_voucher(call_obj, random_pick=False) if call_obj else auth_error()


@router.post("/depot/useFullPotentialItem")
async def depot_use_full_potential_item(request: Request):
    call_obj = await call(request)
    if not call_obj:
        return auth_error()
    item_id = body_id(call_obj.body, "itemId", "id")
    inst_id = body_id(call_obj.body, "instId", "instanceId", default="0")
    char_id = body_id(call_obj.body, "charId")
    if not item_id or not _consume(call_obj, item_id, inst_id, 1):
        return fail("full-potential voucher not owned")
    chars = as_dict(ensure_dict(call_obj.user, "troop").get("chars"))
    target = None
    if char_id:
        target = next((char for char in chars.values() if isinstance(char, dict) and char.get("charId") == char_id), None)
    elif item_id.startswith("voucher_full_"):
        suffix = item_id.removeprefix("voucher_full_")
        target = next(
            (
                char
                for char in chars.values()
                if isinstance(char, dict) and suffix in str(char.get("charId") or "").replace("char_", "")
            ),
            None,
        )
    if not isinstance(target, dict):
        return fail("target operator required")
    target["potentialRank"] = 6
    call_obj.touch("troop")
    result = finish(call_obj, "troop")
    result["charGet"] = {"charId": target.get("charId"), "charInstId": as_int(target.get("instId"), 0), "potentialRank": 6}
    return result
