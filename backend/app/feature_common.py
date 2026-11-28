"""Shared protocol primitives for the feature-domain routers.

Every function in this module is deliberately small and side-effect free for
the caller.  The domain routers decide which account fields they mutate and
then persist the account once through :func:`ok` or :func:`fail`.
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from fastapi import Request

from . import grants
from .db import now_ts
from .services import account_by_token, player_data, save_player


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def clone(value: Any) -> Any:
    return copy.deepcopy(value)


def ensure_dict(parent: dict, key: str) -> dict:
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def ensure_list(parent: dict, key: str) -> list:
    value = parent.get(key)
    if not isinstance(value, list):
        value = []
        parent[key] = value
    return value


def unique_append(values: list, value: Any) -> bool:
    if value in values:
        return False
    values.append(value)
    return True


def first_value(body: dict, *names: str, default: Any = "") -> Any:
    for name in names:
        value = body.get(name)
        if value is not None and value != "":
            return value
    return default


def body_id(body: dict, *names: str, default: str = "") -> str:
    return str(first_value(body, *names, default=default) or default)


def empty_delta() -> dict:
    return {"deleted": {}, "modified": {}}


def merge_delta(target: dict, source: dict | None) -> dict:
    if not isinstance(source, dict):
        return target
    for key, value in as_dict(source.get("deleted")).items():
        target.setdefault("deleted", {})[key] = value
    for key, value in as_dict(source.get("modified")).items():
        target.setdefault("modified", {})[key] = value
    return target


@dataclass
class FeatureCall:
    request: Request
    account: dict
    user: dict
    body: dict
    path: str
    action: str
    modified_keys: set[str] = field(default_factory=set)
    delta: dict = field(default_factory=empty_delta)
    extra: dict = field(default_factory=dict)
    respond: bool = True
    save: bool = True

    def touch(self, *keys: str) -> None:
        for key in keys:
            if key:
                self.modified_keys.add(key)

    def mark(self, *keys: str) -> None:
        self.modified_keys.update(key for key in keys if key)

    def payload(self) -> dict:
        result = {"result": 0}
        result.update(self.extra)
        return result


def _action_from_path(path: str) -> str:
    parts = [part for part in str(path or "").strip("/").split("/") if part]
    return "/".join(parts[1:]) if len(parts) > 1 else "index"


async def call(request: Request, path: str = "") -> FeatureCall | None:
    token = request.headers.get("secret") or request.headers.get("token") or ""
    account = account_by_token(token)
    if not account:
        return None
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return FeatureCall(
        request=request,
        account=account,
        user=player_data(account),
        body=body,
        path=path or request.url.path,
        action=_action_from_path(path or request.url.path),
    )


def auth_error() -> dict:
    return {"result": 2, "error": "invalid token", "playerDataDelta": empty_delta()}


def apply_items(call_obj: FeatureCall, items: Iterable[dict], source: str) -> list[dict]:
    normalized, delta = grants.apply_items(call_obj.user, list(items), source=source, now=now_ts())
    merge_delta(call_obj.delta, delta)
    call_obj.mark(*(as_dict(delta.get("modified")).keys()))
    return normalized


def take_items(call_obj: FeatureCall, items: Iterable[dict], source: str) -> tuple[list[dict], dict]:
    revoked, debt, delta = grants.revoke_items(call_obj.user, list(items), source=source, now=now_ts())
    merge_delta(call_obj.delta, delta)
    call_obj.mark(*(as_dict(delta.get("modified")).keys()))
    return revoked, debt


def delta_keys(call_obj: FeatureCall, *keys: str) -> None:
    modified = call_obj.delta.setdefault("modified", {})
    for key in keys:
        if key in call_obj.user:
            modified[key] = call_obj.user[key]
    call_obj.mark(*keys)


def set_deleted(call_obj: FeatureCall, key: str, value: Any) -> None:
    call_obj.delta.setdefault("deleted", {})[key] = value


def finish(call_obj: FeatureCall, *keys: str, save: bool | None = None) -> dict:
    if keys:
        delta_keys(call_obj, *keys)
    for key in call_obj.modified_keys:
        if key in call_obj.user:
            call_obj.delta.setdefault("modified", {})[key] = call_obj.user[key]
    if save is None:
        save = call_obj.save
    if save:
        save_player(call_obj.account, call_obj.user)
    result = call_obj.payload()
    result["playerDataDelta"] = call_obj.delta
    return result


def fail(message: str, code: int = 1) -> dict:
    return {"result": code, "error": message, "playerDataDelta": empty_delta()}


def battle_id(prefix: str, account: dict) -> str:
    return f"{prefix}-{account.get('uid', 0)}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"


def stage_ap_cost(stage: dict | None, default: int = 0) -> int:
    if not isinstance(stage, dict):
        return default
    value = stage.get("apCost")
    if value is None:
        value = stage.get("apCostConst")
    return max(0, as_int(value, default))


def consume_status(call_obj: FeatureCall, key: str, amount: int) -> bool:
    status = ensure_dict(call_obj.user, "status")
    have = as_int(status.get(key), 0)
    if amount < 0 or have < amount:
        return False
    status[key] = have - amount
    call_obj.touch("status")
    return True


def consume_inventory(call_obj: FeatureCall, item_id: str, amount: int) -> bool:
    inventory = ensure_dict(call_obj.user, "inventory")
    have = as_int(inventory.get(item_id), 0)
    if amount < 0 or have < amount:
        return False
    left = have - amount
    if left:
        inventory[item_id] = left
    else:
        inventory.pop(item_id, None)
    call_obj.touch("inventory")
    return True


def consume_consumable(call_obj: FeatureCall, item_id: str, amount: int = 1, inst_id: str = "0") -> bool:
    consumable = ensure_dict(call_obj.user, "consumable")
    stack = consumable.get(item_id)
    if not isinstance(stack, dict):
        return False
    inst = str(inst_id or "0")
    slot = stack.get(inst)
    if not isinstance(slot, dict):
        for key, value in stack.items():
            if isinstance(value, dict) and as_int(value.get("count"), 0) > 0:
                inst, slot = str(key), value
                break
    if not isinstance(slot, dict):
        return False
    have = as_int(slot.get("count"), 0)
    if have < amount:
        return False
    slot["count"] = have - amount
    call_obj.touch("consumable")
    return True


def state_bucket(call_obj: FeatureCall, root_key: str, *path: str, create: bool = True) -> dict:
    node = call_obj.user.get(root_key)
    if not isinstance(node, dict):
        if not create:
            return {}
        node = {}
        call_obj.user[root_key] = node
    for part in path:
        if not isinstance(node.get(part), dict):
            if not create:
                return {}
            node[part] = {}
        node = node[part]
    return node


def local_tick(call_obj: FeatureCall, root_key: str) -> dict:
    state = ensure_dict(call_obj.user, root_key)
    state["lastAction"] = call_obj.action
    state["lastActionTs"] = now_ts()
    call_obj.touch(root_key)
    return state
