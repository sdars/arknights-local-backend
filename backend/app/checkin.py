"""Cumulative (openServerV3) check-in state helpers."""


DEFAULT_GROUP = "new_login_g_1"
MAX_DAYS = 14


def _dict(parent, key):
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def open_server_checkin(user):
    v3 = _dict(user, "openServerV3")
    checkin = _dict(v3, "checkIn")
    group_id = str(checkin.get("groupId") or DEFAULT_GROUP)
    checkin["groupId"] = group_id
    history = checkin.get("history")
    if not isinstance(history, list):
        history = []
        checkin["history"] = history
    normalized = []
    for value in history:
        try:
            normalized.append(1 if int(value) else 0)
        except (TypeError, ValueError):
            normalized.append(1)
    if normalized != history:
        history[:] = normalized
    return checkin, history


def ensure_state(user):
    """Keep the 14-day cumulative check-in in step with daily sign-ins.

    ``history[i]`` is 1 for an unclaimed day and 0 once its reward was
    taken.  The length is the number of days the player has accumulated.
    Older saves only recorded the classic ``checkIn.checkInHistory``, so
    backfill the missing cumulative days instead of leaving them absent
    (the client renders absent days as already claimed).
    """
    checkin, history = open_server_checkin(user)
    daily = _dict(user, "checkIn").get("checkInHistory")
    signed_days = len(daily) if isinstance(daily, list) else 0
    signed_days = max(0, min(MAX_DAYS, signed_days))
    while len(history) < signed_days:
        history.append(1)
    return checkin, history
