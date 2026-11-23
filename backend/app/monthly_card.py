import time

from . import game_data, grants


DURATION_DAYS = 30


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def day_index(ts=None):
    ts = int(ts or time.time())
    return int(time.strftime("%Y%m%d", time.localtime(ts)))


def current_card_id():
    table = game_data.excel("checkin_table.json") or {}
    return str(table.get("currentMonthlySubId") or "mCard_1")


def daily_items(card_id, now=None):
    now = int(now or time.time())
    table = game_data.excel("checkin_table.json") or {}
    groups = (table.get("monthlySubItem") or {}).get(str(card_id or "")) or []
    selected = None
    for group in groups:
        if not isinstance(group, dict):
            continue
        start = _int(group.get("startTime"), 0)
        end = _int(group.get("endTime"), 0)
        if start <= now <= end:
            selected = group
            break
    if selected is None and groups:
        selected = groups[-1]
    items = []
    for item in (selected or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        item_type = str(item.get("type") or "")
        count = _int(item.get("count"), 0)
        if item_id and item_type and count > 0:
            items.append({"id": item_id, "type": item_type, "count": count})
    return items


def is_active(user, now=None):
    now = int(now or time.time())
    status = (user or {}).get("status") or {}
    return (
        _int(status.get("monthlySubscriptionStartTime"), 0) > 0
        and _int(status.get("monthlySubscriptionEndTime"), 0) > now
    )


def adjust(user, days, now=None):
    now = int(now or time.time())
    status = user.setdefault("status", {})
    start = _int(status.get("monthlySubscriptionStartTime"), 0)
    end = _int(status.get("monthlySubscriptionEndTime"), 0)
    delta = int(days) * 86400
    if days > 0:
        if start <= 0:
            start = now
        end = max(now, end) + delta
    else:
        end = max(0, end + delta)
        if end <= now:
            start = 0
            end = 0
    status["monthlySubscriptionStartTime"] = start
    status["monthlySubscriptionEndTime"] = end
    status["tipMonthlyCardExpireTs"] = end
    return status


def activate(user, card_id=None, now=None, platform=None):
    now = int(now or time.time())
    card_id = str(card_id or current_card_id())
    status = user.setdefault("status", {})
    start = _int(status.get("monthlySubscriptionStartTime"), 0)
    end = _int(status.get("monthlySubscriptionEndTime"), 0)
    if start <= 0:
        status["monthlySubscriptionStartTime"] = now
    new_end = max(now, end) + DURATION_DAYS * 86400
    status["monthlySubscriptionEndTime"] = new_end
    status["tipMonthlyCardExpireTs"] = new_end
    granted, delta = grant_daily(user, card_id, now, platform=platform)
    delta.setdefault("deleted", {})
    delta.setdefault("modified", {})["status"] = status
    return granted, delta, new_end


def grant_daily(user, card_id=None, now=None, platform=None):
    now = int(now or time.time())
    card_id = str(card_id or current_card_id())
    if not is_active(user, now):
        return [], grants.empty_delta()
    local = user.setdefault("localMonthlySub", {})
    today = day_index(now)
    if _int(local.get("lastGrantDay"), 0) == today:
        return [], grants.empty_delta()
    items = daily_items(card_id, now)
    if not items:
        return [], grants.empty_delta()
    granted, delta = grants.apply_items(
        user, items, source="monthly-card", now=now, platform=platform
    )
    local["lastGrantDay"] = today
    local["lastGrantTs"] = now
    local["cardId"] = card_id
    delta.setdefault("deleted", {})
    delta.setdefault("modified", {})
    return granted, delta
