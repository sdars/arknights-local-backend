import copy
import json
import time

from . import gift_catalog, grants
from .shop_fixtures import load_shop


def _gp_payload():
    payload = load_shop("GPGoodList.json")
    return copy.deepcopy(payload) if isinstance(payload, dict) else {}


def _iter_packages(payload):
    for group_key in ("weeklyGroup", "monthlyGroup"):
        group = payload.get(group_key) or {}
        for good_id, row in (group.get("packages") or {}).items():
            if isinstance(row, dict):
                yield group_key, str(good_id), row
    for list_key in ("monthlySub", "levelGP", "oneTimeGP", "chooseGroup", "conditionChooseGroup", "condtionTriggerGroup"):
        for row in payload.get(list_key) or []:
            if isinstance(row, dict) and row.get("goodId"):
                yield list_key, str(row["goodId"]), row


def gp_entry(good_id):
    good_id = str(good_id or "")
    payload = _gp_payload()
    for _, current_id, row in _iter_packages(payload):
        if current_id == good_id:
            return row
    return {}


def buy_count(user, good_id):
    return gift_catalog.purchase_count(user or {}, good_id)


def _available(row, user, good_id):
    avail = row.get("availCount")
    if avail is None:
        return True
    try:
        avail = int(avail)
    except (TypeError, ValueError):
        return True
    if avail < 0:
        return True
    return buy_count(user, good_id) < avail


def decorate_gp_payload(user):
    payload = _gp_payload()
    for _, good_id, row in _iter_packages(payload):
        items = row.get("items")
        if not isinstance(items, list):
            items = gift_catalog.items(good_id)
        if not items:
            items = gift_catalog.items(good_id)
        row["items"] = items
        # Some client builds read the detail panel through the view-model
        # name.  Keep both keys populated; extra keys are ignored by Json.NET.
        row["itemList"] = [dict(item) for item in items]
        row["buyCount"] = buy_count(user, good_id)
        row.setdefault("useGpTicket", bool(row.get("gpTicketIdList")))
        tickets = row.get("gpTicketIdList") or []
        if tickets and not row.get("gpTicketId"):
            row["gpTicketId"] = str(tickets[0])
        row.setdefault("imgDisplayDataList", [])
    payload["goodList"] = payload.get("goodList") or []
    return payload


def purchase_state(user, requested=None):
    result = {}
    requested = requested if isinstance(requested, dict) else {}
    wanted = set()
    for key in ("GP", "gp", "giftShop"):
        values = requested.get(key)
        if isinstance(values, list):
            wanted.update(str(value) for value in values)
    for good_id in gift_catalog.catalog():
        if wanted and good_id not in wanted:
            continue
        count = buy_count(user, good_id)
        if count:
            result[good_id] = count
    return result


def _deduct_diamond(user, count, platform):
    status = user.setdefault("status", {})
    key = grants.diamond_status_key(user, platform)
    balance = int(status.get(key) or 0)
    if balance < count:
        return False
    status[key] = balance - count
    return True


def _deduct_ticket(user, ticket_id):
    inventory = user.setdefault("inventory", {})
    ticket_id = str(ticket_id or "")
    if not ticket_id:
        return False
    for source in (inventory, (user.get("consumable") or {})):
        value = source.get(ticket_id) if isinstance(source, dict) else None
        if isinstance(value, dict):
            value = value.get("0", {}).get("count") if isinstance(value.get("0"), dict) else value.get("count")
        if value is not None:
            try:
                amount = int(value)
            except (TypeError, ValueError):
                continue
            if amount > 0:
                if amount == 1:
                    source.pop(ticket_id, None)
                else:
                    if isinstance(source.get(ticket_id), dict):
                        slot = source[ticket_id].setdefault("0", {})
                        slot["count"] = amount - 1
                    else:
                        source[ticket_id] = amount - 1
                return True
    return False


def buy_gp(user, good_id, platform=None, now=None):
    now = int(now or time.time())
    good_id = str(good_id or "")
    row = gp_entry(good_id)
    if not row:
        return {"result": 1, "error": "gift package not found", "goodId": good_id}
    if not _available(row, user, good_id):
        return {"result": 1, "error": "purchase limit reached", "goodId": good_id}
    items = gift_catalog.items(good_id) or list(row.get("items") or [])
    if not items:
        return {"result": 1, "error": "gift package content not configured", "goodId": good_id}
    currency = str(row.get("currencyUnit") or "")
    price = int(row.get("price") or 0)
    if currency == "DIAMOND":
        if not _deduct_diamond(user, price, platform):
            return {"result": 1, "error": "insufficient originium", "goodId": good_id}
    elif currency == "CASH":
        return {"result": 1, "error": "cash order required", "goodId": good_id}
    elif price > 0:
        return {"result": 1, "error": "unsupported currency", "goodId": good_id, "currency": currency}
    granted, delta = grants.apply_items(user, items, source="shop-gp", now=now, platform=platform)
    gift_catalog.record_purchase(user, good_id, 1)
    delta.setdefault("deleted", {})
    delta.setdefault("modified", {})
    delta["modified"]["status"] = user.get("status", {})
    delta["modified"]["shop"] = user.get("shop", {})
    return {"result": 0, "goodId": good_id, "items": granted, "playerDataDelta": delta}


def buy_gp_with_ticket(user, good_id, ticket_id=None, platform=None, now=None):
    now = int(now or time.time())
    good_id = str(good_id or "")
    row = gp_entry(good_id)
    if not row:
        return {"result": 1, "error": "gift package not found", "goodId": good_id}
    if not _available(row, user, good_id):
        return {"result": 1, "error": "purchase limit reached", "goodId": good_id}
    tickets = [str(value) for value in row.get("gpTicketIdList") or [] if value]
    ticket_id = str(ticket_id or (tickets[0] if tickets else ""))
    if tickets and ticket_id not in tickets:
        return {"result": 1, "error": "ticket not accepted", "goodId": good_id}
    if not _deduct_ticket(user, ticket_id):
        return {"result": 1, "error": "ticket not found", "goodId": good_id, "ticketId": ticket_id}
    items = gift_catalog.items(good_id) or list(row.get("items") or [])
    granted, delta = grants.apply_items(user, items, source="shop-gp-ticket", now=now, platform=platform)
    gift_catalog.record_purchase(user, good_id, 1)
    delta.setdefault("deleted", {})
    delta.setdefault("modified", {})
    delta["modified"]["inventory"] = user.get("inventory", {})
    delta["modified"]["shop"] = user.get("shop", {})
    return {"result": 0, "goodId": good_id, "items": granted, "playerDataDelta": delta}
