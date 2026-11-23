import asyncio
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import APP_DIR, ASSET_CDN, SERVER_NAME, CLIENT_VERSION, RES_VERSION
from ..db import connect, recent_logs
from ..security import verify_password, new_token
from ..services import account_stats, list_accounts, create_account
from .. import (
    game_data,
    gift_catalog,
    grants,
    mail_service,
    payments,
    runtime_settings,
    selfcheck,
    vouchers,
)
from .. import protocol_catalog
from .. import il2cpp_dump

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def admin_name(request: Request):
    return request.session.get("admin")


def need_login(request: Request):
    if not admin_name(request):
        return RedirectResponse("/admin/login", status_code=303)
    return None


def _account(uid):
    with connect() as conn:
        return conn.execute("SELECT * FROM accounts WHERE uid=?", (int(uid),)).fetchone()


def _user(account):
    return json.loads(account["user_json"])


def _save_user(uid, user):
    with connect() as conn:
        conn.execute(
            "UPDATE accounts SET user_json=?, nickname=? WHERE uid=?",
            (
                json.dumps(user, ensure_ascii=False, separators=(",", ":")),
                user.get("status", {}).get("nickName") or "博士",
                int(uid),
            ),
        )


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _optional_int(value):
    text = str(value or "").strip()
    return _int(text) if text else None


def _flash(request, message):
    request.session["admin_flash"] = str(message)


def _take_flash(request):
    return request.session.pop("admin_flash", "")


def _phase_number(value):
    text = str(value or "PHASE_0")
    if text.startswith("PHASE_"):
        return _int(text.split("_", 1)[1], 0)
    return _int(text, 0)


def _sync_char_skills(char, meta):
    phase = _int(char.get("evolvePhase"), 0)
    skills = char.get("skills")
    if not isinstance(skills, list):
        skills = []
        char["skills"] = skills
    meta_skills = {str(skill.get("skillId")): skill for skill in meta.get("skills") or [] if isinstance(skill, dict)}
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        source = meta_skills.get(str(skill.get("skillId"))) or {}
        needed = _phase_number((source.get("unlockCond") or {}).get("phase"))
        skill["unlock"] = 1 if phase >= needed else 0
    unlocked = [index for index, skill in enumerate(skills) if isinstance(skill, dict) and int(skill.get("unlock") or 0)]
    default_index = _int(char.get("defaultSkillIndex"), -1)
    if default_index not in unlocked:
        char["defaultSkillIndex"] = unlocked[0] if unlocked else -1


def _account_rows(accounts):
    rows = []
    for account in accounts:
        try:
            user = json.loads(account["user_json"])
        except Exception:
            user = {}
        status = user.get("status") or {}
        rows.append(
            {
                **dict(account),
                "level": _int(status.get("level"), 1),
                "gold": _int(status.get("gold"), 0),
                "diamond": _int(status.get("androidDiamond"), 0) + _int(status.get("iosDiamond"), 0),
                "characters": len((user.get("troop") or {}).get("chars") or {}),
                "last_login_at": account["last_login_at"],
            }
        )
    return rows


def _orders_for(account_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE account_id=? ORDER BY created_at DESC LIMIT 30",
            (int(account_id),),
        ).fetchall()


def _asset_diagnostics():
    settings = runtime_settings.values()
    root_text = str(settings.get("asset_root") or "").strip()
    result = {
        "root": root_text,
        "exists": False,
        "files": 0,
        "bytes": 0,
        "latest_at": 0,
        "sample": [],
    }
    if not root_text:
        return result
    try:
        root = Path(root_text).expanduser().resolve()
    except Exception:
        return result
    result["root"] = str(root)
    result["exists"] = root.is_dir()
    if not result["exists"]:
        return result
    samples = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        result["files"] += 1
        result["bytes"] += stat.st_size
        result["latest_at"] = max(result["latest_at"], stat.st_mtime)
        if len(samples) < 20:
            samples.append(
                {
                    "path": str(path.relative_to(root)),
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                }
            )
        if result["files"] >= 20000:
            break
    result["sample"] = samples
    return result


def _mail_instance_rows(limit=100):
    with connect() as conn:
        rows = conn.execute(
            """SELECT m.*, a.phone, a.nickname FROM mail_instances m
               LEFT JOIN accounts a ON a.uid=m.uid
               ORDER BY m.create_at DESC, m.mail_id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["items"] = mail_service._items(item.get("items_json"))
        item["item_rows"] = gift_catalog.item_rows(item["items"])
        item["style"] = mail_service._json(item.get("style_json"), {})
        result.append(item)
    return result


def _mail_form_values(form):
    items = None
    raw_ids = [str(value).strip() for value in form.getlist("item_id") if str(value).strip()]
    if raw_ids:
        catalog = game_data.resource_catalog()
        raw_counts = [str(value) for value in form.getlist("item_count")]
        items = []
        for index, item_id in enumerate(raw_ids):
            meta = catalog.get(item_id)
            if not meta:
                raise ValueError(f"不存在的资源 ID：{item_id}")
            try:
                count = int(raw_counts[index]) if index < len(raw_counts) else 1
            except (TypeError, ValueError):
                raise ValueError(f"资源数量无效：{item_id}")
            if count <= 0 or count > 999999:
                raise ValueError(f"资源数量必须在 1-999999 之间：{item_id}")
            items.append({"id": item_id, "type": meta["type"], "count": count})
    raw_items = str(form.get("items") or "").strip()
    if items is None and raw_items:
        try:
            items = json.loads(raw_items)
        except json.JSONDecodeError as exc:
            raise ValueError(f"附件 JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列") from exc
        if not isinstance(items, (list, dict)):
            raise ValueError("附件 JSON 必须是数组或包含 items 的对象")
        if isinstance(items, dict):
            items = items.get("items") or []
        catalog = game_data.resource_catalog()
        normalized = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("附件条目必须是对象")
            item_id = str(item.get("id") or "").strip()
            meta = catalog.get(item_id)
            if not meta:
                raise ValueError(f"不存在的资源 ID：{item_id or '(空)'}")
            try:
                count = int(item.get("count") or 1)
            except (TypeError, ValueError):
                raise ValueError(f"资源数量无效：{item_id}")
            if count <= 0 or count > 999999:
                raise ValueError(f"资源数量必须在 1-999999 之间：{item_id}")
            normalized.append({"id": item_id, "type": meta["type"], "count": count})
        items = normalized
    return {
        "template_id": _optional_int(form.get("template_id")),
        "sender": str(form.get("sender") or "").strip() or None,
        "subject": str(form.get("subject") or "").strip() or None,
        "content": str(form.get("content") or "") or None,
        "items": items,
        "expire_days": _optional_int(form.get("expire_days")),
    }


def _account_page_context(account):
    user = _user(account)
    item_catalog = game_data.item_catalog()
    char_catalog = game_data.character_catalog()
    skin_catalog = game_data.skin_catalog()
    avatar_catalog = game_data.avatar_catalog()
    inventory_rows = []
    for item_id, count in (user.get("inventory") or {}).items():
        meta = item_catalog.get(str(item_id)) or {}
        inventory_rows.append(
            {
                "id": str(item_id),
                "count": _int(count, 0),
                "name": meta.get("name") or str(item_id),
                "type": meta.get("type") or "MATERIAL",
                "rarity": meta.get("rarity") or "",
            }
        )
    inventory_rows.sort(key=lambda row: (row["type"], row["name"], row["id"]))
    character_rows = []
    for inst_id, char in (user.get("troop", {}).get("chars") or {}).items():
        if not isinstance(char, dict):
            continue
        char_id = char.get("charId") or ""
        meta = char_catalog.get(char_id) or {}
        character_rows.append(
            {
                "instId": str(inst_id),
                "charId": char_id,
                "name": meta.get("name") or char_id,
                "rarity": meta.get("rarity") or "",
                "level": _int(char.get("level"), 1),
                "evolvePhase": _int(char.get("evolvePhase"), 0),
                "potentialRank": _int(char.get("potentialRank"), 0),
                "mainSkillLvl": _int(char.get("mainSkillLvl"), 1),
                "favorPoint": _int(char.get("favorPoint"), 0),
                "skin": char.get("skin") or "",
                "voiceLan": char.get("voiceLan") or "",
            }
        )
    character_rows.sort(key=lambda row: (row["rarity"], row["name"], row["instId"]), reverse=True)
    squads = []
    for squad_id, squad in (user.get("troop", {}).get("squads") or {}).items():
        if not isinstance(squad, dict):
            continue
        raw_slots = squad.get("slots") or []
        slot_rows = []
        for index in range(12):
            raw = raw_slots[index] if index < len(raw_slots) and isinstance(raw_slots[index], dict) else None
            inst_id = str((raw or {}).get("charInstId") or "")
            char = (user.get("troop", {}).get("chars") or {}).get(inst_id) or {}
            meta = char_catalog.get(char.get("charId") or "") or {}
            slot_rows.append(
                {
                    "index": index,
                    "instId": inst_id,
                    "name": meta.get("name") or char.get("charId") or "",
                }
            )
        squads.append({"id": str(squad_id), "name": squad.get("name") or "", "slots": slot_rows})
    squads.sort(key=lambda item: _int(item["id"], 9999))
    skins = []
    for skin_id, ts in ((user.get("skin") or {}).get("characterSkins") or {}).items():
        meta = skin_catalog.get(str(skin_id)) or {}
        skins.append(
            {
                "id": str(skin_id),
                "name": meta.get("name") or str(skin_id),
                "charId": meta.get("charId") or "",
                "ts": _int(ts, 0),
            }
        )
    avatars = []
    for avatar_id, value in ((user.get("avatar") or {}).get("avatar_icon") or {}).items():
        meta = avatar_catalog.get(str(avatar_id)) or {}
        avatars.append(
            {
                "id": str(avatar_id),
                "name": meta.get("name") or str(avatar_id),
                "type": meta.get("type") or "",
                "ts": _int(value.get("ts"), 0) if isinstance(value, dict) else 0,
            }
        )
    mails = mail_service.list_for_uid(account["uid"])
    for mail in mails:
        mail["item_rows"] = gift_catalog.item_rows(mail.get("items") or [])
    mail_templates = mail_service.templates()
    for template in mail_templates:
        template["resource_items"] = gift_catalog.item_rows(template.get("items") or [])
    orders = []
    for raw_order in _orders_for(account["id"]):
        order = dict(raw_order)
        try:
            granted_items = json.loads(order.get("granted_items_json") or "[]")
        except Exception:
            granted_items = []
        order["gift_items"] = gift_catalog.item_rows(granted_items)
        order["gift_name"] = gift_catalog.display_name(order.get("good_id"))
        orders.append(order)
    granted_orders = sum(1 for order in orders if _int(order["granted"], 0))
    debt_rows = []
    for debt in grants.debt_rows(user):
        parts = str(debt["key"]).split(":")
        item_type = parts[0] if parts else ""
        item_id = parts[-1] if parts else debt["key"]
        detail = gift_catalog.item_rows(
            [{"id": item_id, "type": item_type, "count": debt["count"]}]
        )
        debt_rows.append(
            {
                **debt,
                "id": item_id,
                "type": item_type,
                "name": detail[0]["name"] if detail else item_id,
            }
        )
    return {
        "account": account,
        "user": user,
        "status": user.get("status") or {},
        "inventory_rows": inventory_rows,
        "character_rows": character_rows,
        "squads": squads,
        "skins": sorted(skins, key=lambda row: row["id"]),
        "avatars": sorted(avatars, key=lambda row: row["id"]),
        "item_catalog": item_catalog,
        "char_catalog": char_catalog,
        "skin_catalog": skin_catalog,
        "avatar_catalog": avatar_catalog,
        "mails": mails,
        "mail_templates": mail_templates,
        "orders": orders,
        "debt_rows": debt_rows,
        "summary": {
            "inventory": len(inventory_rows),
            "characters": len(character_rows),
            "skins": len(skins),
            "avatars": len(avatars),
            "squads": len(squads),
            "mails": len([mail for mail in mails if not int(mail.get("removed") or 0)]),
            "unread_mails": len([mail for mail in mails if not int(mail.get("removed") or 0) and not int(mail.get("state") or 0)]),
            "orders": len(orders),
            "granted_orders": granted_orders,
            "debt": len(debt_rows),
        },
    }



def _order_rows(q="", status="", platform="", limit=500):
    where = []
    params = []
    if q:
        needle = f"%{q.strip()}%"
        where.append(
            "(o.order_id LIKE ? OR o.good_id LIKE ? OR o.product_name LIKE ? OR a.phone LIKE ? OR a.nickname LIKE ? OR CAST(o.uid AS TEXT) LIKE ?)"
        )
        params.extend([needle] * 6)
    if status == "pending":
        where.append("o.status=0 AND o.revoked=0")
    elif status == "approved":
        where.append("o.status=1 AND o.revoked=0")
    elif status == "rejected":
        where.append("o.status=2")
    elif status == "revoked":
        where.append("o.revoked=1")
    if platform in {"android", "ios"}:
        where.append("o.platform=?")
        params.append(platform)
    sql = """SELECT o.*, a.phone, a.nickname, a.platform AS account_platform,
                    v.code AS voucher_code, v.name AS voucher_name
             FROM orders o LEFT JOIN accounts a ON a.id=o.account_id
             LEFT JOIN vouchers v ON v.id=o.voucher_id"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY o.created_at DESC, o.order_id DESC LIMIT ?"
    params.append(int(limit))
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for raw in rows:
        row = dict(raw)
        try:
            items = json.loads(row.get("granted_items_json") or "[]")
        except Exception:
            items = []
        if not items:
            try:
                items = payments.order_items(row)
            except Exception:
                items = []
        row["gift_items"] = gift_catalog.item_rows(items)
        try:
            row["revoked_items"] = gift_catalog.item_rows(
                json.loads(row.get("revoked_items_json") or "[]")
            )
        except Exception:
            row["revoked_items"] = []
        try:
            row["revoke_debt"] = json.loads(row.get("revoke_debt_json") or "{}")
        except Exception:
            row["revoke_debt"] = {}
        row["gift_name"] = gift_catalog.display_name(row.get("good_id")) or row.get("product_name")
        result.append(row)
    return result


def _select_accounts(form):
    scope = str(form.get("scope") or "uids")
    with connect() as conn:
        if scope == "all":
            return conn.execute("SELECT * FROM accounts ORDER BY uid").fetchall()
        if scope == "filter":
            clauses = []
            params = []
            uid_min = _int(form.get("uid_min"), 0)
            uid_max = _int(form.get("uid_max"), 0)
            if uid_min:
                clauses.append("uid>=?")
                params.append(uid_min)
            if uid_max:
                clauses.append("uid<=?")
                params.append(uid_max)
            keyword = str(form.get("keyword") or "").strip()
            if keyword:
                clauses.append("(phone LIKE ? OR nickname LIKE ?)")
                params.extend([f"%{keyword}%", f"%{keyword}%"])
            platform = str(form.get("platform") or "").strip()
            if platform in {"android", "ios"}:
                clauses.append("platform=?")
                params.append(platform)
            sql = "SELECT * FROM accounts"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY uid"
            return conn.execute(sql, params).fetchall()
        raw = str(form.get("uids") or "")
        uids = []
        for part in raw.replace(";", ",").replace("\n", ",").split(","):
            uid = _int(part.strip(), 0)
            if uid and uid not in uids:
                uids.append(uid)
        if not uids:
            return []
        placeholders = ",".join("?" for _ in uids)
        return conn.execute(
            f"SELECT * FROM accounts WHERE uid IN ({placeholders}) ORDER BY uid", uids
        ).fetchall()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    with connect() as conn:
        row = conn.execute("SELECT * FROM admin_users WHERE username=?", (username,)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return templates.TemplateResponse(request, "login.html", {"error": "用户名或密码错误"}, status_code=401)
    request.session["admin"] = username
    return RedirectResponse("/admin", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    stats = account_stats()
    accounts = list_accounts()[-8:]
    logs = recent_logs(12)
    development = await run_in_threadpool(selfcheck.collect_status)
    gate_done = sum(1 for gate in development["gates"] if gate["state"] == "done")
    settings = runtime_settings.values()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "admin": admin_name(request),
            "stats": stats,
            "accounts": accounts,
            "logs": logs,
            "server_name": settings["server_name"],
            "client_version": settings["client_version"],
            "res_version": settings["res_version"],
            "development": development,
            "gate_done": gate_done,
        },
    )


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request, q: str = ""):
    redirect = need_login(request)
    if redirect:
        return redirect
    rows = _account_rows(list_accounts())
    needle = q.strip().lower()
    if needle:
        rows = [
            row
            for row in rows
            if needle in str(row["uid"]).lower()
            or needle in str(row["phone"]).lower()
            or needle in str(row["nickname"]).lower()
            or needle in str(row["token"]).lower()
        ]
    return templates.TemplateResponse(
        request,
        "accounts.html",
        {"admin": admin_name(request), "accounts": rows, "q": q, "message": _take_flash(request)},
    )


@router.post("/accounts/create")
async def account_create(
    request: Request,
    phone: str = Form(...),
    nickname: str = Form("博士"),
    token: str = Form(""),
):
    redirect = need_login(request)
    if redirect:
        return redirect
    create_account(phone.strip(), token.strip() or None, nickname.strip() or "博士")
    return RedirectResponse("/admin/accounts", status_code=303)


@router.get("/accounts/{uid}", response_class=HTMLResponse)
async def account_edit(request: Request, uid: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    context = _account_page_context(account)
    context["admin"] = admin_name(request)
    context["message"] = _take_flash(request)
    return templates.TemplateResponse(request, "account_edit.html", context)


@router.post("/accounts/{uid}/update")
async def account_update(
    request: Request,
    uid: int,
    phone: str = Form(...),
    nickname: str = Form(...),
    token: str = Form(...),
    banned: str = Form("0"),
):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if account:
        user = _user(account)
        user.setdefault("status", {})["nickName"] = nickname.strip() or "博士"
    try:
        with connect() as conn:
            conn.execute(
                "UPDATE accounts SET phone=?,nickname=?,token=?,banned=? WHERE uid=?",
                (phone.strip(), nickname.strip(), token.strip(), 1 if banned == "1" else 0, uid),
            )
            if account:
                conn.execute(
                    "UPDATE accounts SET user_json=? WHERE uid=?",
                    (json.dumps(user, ensure_ascii=False, separators=(",", ":")), uid),
                )
    except sqlite3.IntegrityError:
        _flash(request, "手机号或 Token 已被其他账号占用")
        return RedirectResponse(f"/admin/accounts/{uid}", status_code=303)
    return RedirectResponse("/admin/accounts", status_code=303)


@router.post("/accounts/{uid}/status")
async def account_status_update(request: Request, uid: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    form = await request.form()
    user = _user(account)
    status = user.setdefault("status", {})
    integer_fields = (
        "level", "exp", "gold", "diamondShard", "androidDiamond", "iosDiamond",
        "payDiamond", "freeDiamond", "gachaTicket", "tenGachaTicket", "classicGachaTicket",
        "classicTenGachaTicket", "recruitLicense", "instantFinishTicket", "practiceTicket",
        "hggShard", "lggShard", "classicShard", "socialPoint", "ap", "maxAp",
        "buyApRemainTimes", "progress",
    )
    for key in integer_fields:
        if key in form:
            status[key] = max(0, _int(form.get(key), _int(status.get(key), 0)))
    status["level"] = max(1, _int(status.get("level"), 1))
    status["maxAp"] = max(1, _int(status.get("maxAp"), 1))
    if "ap" in form:
        status["ap"] = min(_int(status.get("ap"), status["maxAp"]), status["maxAp"])
    for key in (
        "mainStageProgress",
        "secretary",
        "secretarySkinId",
        "avatarId",
        "globalVoiceLan",
        "resume",
    ):
        if key in form:
            status[key] = str(form.get(key) or "")[:200]
    if "monthlySubscriptionStartTime" in form:
        status["monthlySubscriptionStartTime"] = max(0, _int(form.get("monthlySubscriptionStartTime"), 0))
    if "monthlySubscriptionEndTime" in form:
        status["monthlySubscriptionEndTime"] = max(0, _int(form.get("monthlySubscriptionEndTime"), 0))
    if "nickName" in form and str(form.get("nickName") or "").strip():
        status["nickName"] = str(form.get("nickName")).strip()
        with connect() as conn:
            conn.execute("UPDATE accounts SET nickname=? WHERE uid=?", (status["nickName"], uid))
    _save_user(uid, user)
    return RedirectResponse(f"/admin/accounts/{uid}", status_code=303)


@router.post("/accounts/{uid}/inventory")
async def account_inventory_update(request: Request, uid: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    form = await request.form()
    item_id = str(form.get("item_id") or "").strip()
    if not item_id:
        return RedirectResponse(f"/admin/accounts/{uid}", status_code=303)
    mode = str(form.get("mode") or "add")
    item_type = str(form.get("item_type") or "").strip() or game_data.item_type(item_id)
    count = _int(form.get("count"), 1)
    user = _user(account)
    inventory = user.setdefault("inventory", {})
    if mode == "delete":
        inventory.pop(item_id, None)
    elif mode == "set":
        inventory[item_id] = max(0, count)
    else:
        grants.apply_items(
            user,
            [{"id": item_id, "type": item_type, "count": max(1, count)}],
            source="admin-inventory",
        )
    _save_user(uid, user)
    return RedirectResponse(f"/admin/accounts/{uid}#inventory", status_code=303)


@router.post("/accounts/{uid}/grant")
async def account_grant(request: Request, uid: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    form = await request.form()
    item_id = str(form.get("grant_id") or "").strip()
    item_type = str(form.get("grant_type") or "").strip() or game_data.item_type(item_id)
    count = max(1, _int(form.get("grant_count"), 1))
    if item_id:
        if item_type == "CHAR" and not game_data.character(item_id):
            _flash(request, f"未知干员 ID：{item_id}")
            return RedirectResponse(f"/admin/accounts/{uid}#assets", status_code=303)
        user = _user(account)
        grants.apply_items(user, [{"id": item_id, "type": item_type, "count": count}], source="admin-grant")
        _save_user(uid, user)
    return RedirectResponse(f"/admin/accounts/{uid}#assets", status_code=303)


@router.post("/accounts/{uid}/asset/remove")
async def account_asset_remove(request: Request, uid: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    form = await request.form()
    kind = str(form.get("kind") or "")
    asset_id = str(form.get("asset_id") or "")
    user = _user(account)
    if kind == "skin" and asset_id:
        skin = user.setdefault("skin", {})
        (skin.get("characterSkins") or {}).pop(asset_id, None)
        (skin.get("skinTs") or {}).pop(asset_id, None)
        for char in (user.get("troop", {}).get("chars") or {}).values():
            if isinstance(char, dict) and char.get("skin") == asset_id:
                char["skin"] = f"{char.get('charId')}#1"
    elif kind == "avatar" and asset_id:
        avatar = user.setdefault("avatar", {})
        (avatar.get("avatar_icon") or {}).pop(asset_id, None)
        status = user.setdefault("status", {})
        if status.get("avatarId") == asset_id:
            status["avatarId"] = "0"
    else:
        _flash(request, "资产类型或 ID 无效")
        return RedirectResponse(f"/admin/accounts/{uid}#assets", status_code=303)
    _save_user(uid, user)
    return RedirectResponse(f"/admin/accounts/{uid}#assets", status_code=303)


@router.post("/accounts/{uid}/operator/{inst_id}")
async def account_operator_update(request: Request, uid: int, inst_id: str):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    form = await request.form()
    user = _user(account)
    char = (user.get("troop", {}).get("chars") or {}).get(str(inst_id))
    if isinstance(char, dict):
        meta = game_data.character(char.get("charId")) or {}
        if "evolvePhase" in form:
            phases = meta.get("phases") or []
            char["evolvePhase"] = min(max(0, _int(form.get("evolvePhase"), 0)), max(0, len(phases) - 1))
        max_level = 90
        phases = meta.get("phases") or []
        phase = min(_int(char.get("evolvePhase"), 0), max(0, len(phases) - 1))
        if phases:
            max_level = max(1, _int((phases[phase] or {}).get("maxLevel"), 1))
        if "level" in form:
            char["level"] = min(max(1, _int(form.get("level"), 1)), max_level)
        char["level"] = min(max(1, _int(char.get("level"), 1)), max_level)
        if "potentialRank" in form:
            char["potentialRank"] = min(max(0, _int(form.get("potentialRank"), 0)), 5)
        if "mainSkillLvl" in form:
            char["mainSkillLvl"] = min(max(1, _int(form.get("mainSkillLvl"), 1)), 7)
        if "favorPoint" in form:
            char["favorPoint"] = min(max(0, _int(form.get("favorPoint"), 0)), 200)
        if "skin" in form:
            char["skin"] = str(form.get("skin") or "")
        if "voiceLan" in form:
            char["voiceLan"] = str(form.get("voiceLan") or "CN_MANDARIN")
        _sync_char_skills(char, meta)
        _save_user(uid, user)
    return RedirectResponse(f"/admin/accounts/{uid}#operators", status_code=303)


@router.post("/accounts/{uid}/operator-add")
async def account_operator_add(request: Request, uid: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    form = await request.form()
    char_id = str(form.get("char_id") or "").strip()
    count = max(1, _int(form.get("count"), 1))
    if char_id:
        user = _user(account)
        if game_data.character(char_id):
            grants.apply_items(user, [{"id": char_id, "type": "CHAR", "count": count}], source="admin-char")
            _save_user(uid, user)
        else:
            _flash(request, f"未知干员 ID：{char_id}")
    return RedirectResponse(f"/admin/accounts/{uid}#operators", status_code=303)


@router.post("/accounts/{uid}/operator/{inst_id}/delete")
async def account_operator_delete(request: Request, uid: int, inst_id: str):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    user = _user(account)
    troop = user.setdefault("troop", {})
    char = (troop.get("chars") or {}).pop(str(inst_id), None)
    if isinstance(char, dict):
        char_id = char.get("charId")
        if char_id:
            (troop.get("charGroup") or {}).pop(char_id, None)
        ((user.get("building") or {}).get("chars") or {}).pop(str(inst_id), None)
        for squad in (troop.get("squads") or {}).values():
            if not isinstance(squad, dict):
                continue
            slots = squad.get("slots") or []
            for index, slot in enumerate(slots):
                if isinstance(slot, dict) and str(slot.get("charInstId")) == str(inst_id):
                    slots[index] = None
        _save_user(uid, user)
    return RedirectResponse(f"/admin/accounts/{uid}#operators", status_code=303)


@router.post("/accounts/{uid}/squad/{squad_id}")
async def account_squad_update(request: Request, uid: int, squad_id: str):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    form = await request.form()
    user = _user(account)
    squads = user.setdefault("troop", {}).setdefault("squads", {})
    squad = squads.get(str(squad_id))
    if not isinstance(squad, dict):
        squad = {"squadId": str(squad_id), "name": "", "slots": [None] * 12}
        squads[str(squad_id)] = squad
    chars = user.get("troop", {}).get("chars") or {}
    old_slots = squad.get("slots") or []
    slots = []
    for index in range(12):
        raw = str(form.get(f"slot_{index}") or "").strip()
        if not raw or raw in {"0", "none", "null"}:
            slots.append(None)
            continue
        inst_id = str(_int(raw, 0))
        char = chars.get(inst_id)
        if not isinstance(char, dict):
            slots.append(None)
            continue
        old = old_slots[index] if index < len(old_slots) and isinstance(old_slots[index], dict) else {}
        if str(old.get("charInstId")) != inst_id:
            old = {
                "charInstId": int(inst_id),
                "skillIndex": _int(char.get("defaultSkillIndex"), -1),
                "currentEquip": char.get("currentEquip"),
            }
        slots.append(dict(old))
    squad["slots"] = slots
    squad["squadId"] = str(squad_id)
    squad["name"] = str(form.get("name") or squad.get("name") or "")
    user.setdefault("troop", {})["curSquadCount"] = max(int(user.get("troop", {}).get("curSquadCount", 4) or 4), len(squads))
    _save_user(uid, user)
    return RedirectResponse(f"/admin/accounts/{uid}#squads", status_code=303)


@router.post("/accounts/{uid}/squad-add")
async def account_squad_add(request: Request, uid: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    form = await request.form()
    user = _user(account)
    squads = user.setdefault("troop", {}).setdefault("squads", {})
    used = [_int(squad_id, -1) for squad_id in squads.keys()]
    squad_id = str(max(used or [-1]) + 1)
    squads[squad_id] = {
        "squadId": squad_id,
        "name": str(form.get("name") or f"第{len(squads) + 1}编队")[:64],
        "slots": [None] * 12,
    }
    user.setdefault("troop", {})["curSquadCount"] = max(4, len(squads))
    _save_user(uid, user)
    return RedirectResponse(f"/admin/accounts/{uid}#squads", status_code=303)


@router.post("/accounts/{uid}/squad/{squad_id}/delete")
async def account_squad_delete(request: Request, uid: int, squad_id: str):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    user = _user(account)
    squads = user.setdefault("troop", {}).setdefault("squads", {})
    if len(squads) > 1:
        squads.pop(str(squad_id), None)
        _save_user(uid, user)
    return RedirectResponse(f"/admin/accounts/{uid}#squads", status_code=303)


@router.post("/accounts/{uid}/mail/send")
async def account_mail_send(request: Request, uid: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        values = _mail_form_values(form)
    except ValueError as exc:
        _flash(request, str(exc))
        return RedirectResponse(f"/admin/accounts/{uid}#mail", status_code=303)
    result = mail_service.send_mail(uid, **values)
    if result.get("result") != 0:
        _flash(request, result.get("error") or "邮件发送失败")
    return RedirectResponse(f"/admin/accounts/{uid}#mail", status_code=303)


@router.post("/accounts/{uid}/mail/{mail_id}/delete")
async def account_mail_delete(request: Request, uid: int, mail_id: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    mail_service.remove_mail(uid, [mail_id])
    return RedirectResponse(f"/admin/accounts/{uid}#mail", status_code=303)


@router.get("/accounts/{uid}/user.json")
async def account_user_json(request: Request, uid: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return JSONResponse({"error": "account not found"}, status_code=404)
    return JSONResponse(
        _user(account),
        headers={"Content-Disposition": f'attachment; filename="uid-{uid}.json"'},
    )


@router.get("/mail", response_class=HTMLResponse)
async def mail_center(request: Request, edit: int = 0, q: str = ""):
    redirect = need_login(request)
    if redirect:
        return redirect
    templates_list = mail_service.templates()
    for template in templates_list:
        template["resource_items"] = gift_catalog.item_rows(template.get("items") or [])
    edit_template = next((item for item in templates_list if int(item["id"]) == int(edit)), None)
    mails = _mail_instance_rows(200)
    needle = q.strip().lower()
    if needle:
        mails = [
            mail
            for mail in mails
            if needle in str(mail.get("uid")).lower()
            or needle in str(mail.get("phone") or "").lower()
            or needle in str(mail.get("nickname") or "").lower()
            or needle in str(mail.get("subject") or "").lower()
        ]
    return templates.TemplateResponse(
        request,
        "mail_center.html",
        {
            "admin": admin_name(request),
            "mail_templates": templates_list,
            "edit_template": edit_template,
            "mails": mails,
            "accounts": list_accounts(),
            "q": q,
            "message": _take_flash(request),
        },
    )


@router.get("/orders", response_class=HTMLResponse)
async def orders_page(
    request: Request,
    q: str = "",
    status: str = "",
    platform: str = "",
):
    redirect = need_login(request)
    if redirect:
        return redirect
    rows = _order_rows(q=q, status=status, platform=platform)
    with connect() as conn:
        accounts = conn.execute("SELECT * FROM accounts ORDER BY uid").fetchall()
    return templates.TemplateResponse(
        request,
        "orders.html",
        {
            "admin": admin_name(request),
            "active": "orders",
            "orders": rows,
            "accounts": accounts,
            "products": sorted(payments.products(), key=lambda item: (item.get("name") or "", item.get("product_id") or "")),
            "q": q,
            "status_filter": status,
            "platform_filter": platform,
            "message": _take_flash(request),
        },
    )


@router.post("/orders/create")
async def orders_create(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    accounts = _select_accounts(form)
    good_ids = [str(value) for value in form.getlist("good_ids") if value]
    if not accounts or not good_ids:
        _flash(request, "请选择至少一个账号和至少一个商品")
        return RedirectResponse("/admin/orders", status_code=303)
    mode = str(form.get("mode") or "pending")
    note = str(form.get("note") or "").strip()
    platform = str(form.get("platform") or "")
    created = 0
    for account in accounts:
        for good_id in good_ids:
            product = payments.find_product(None, good_id)
            if not product:
                continue
            order = payments.create_order(
                dict(account),
                product["store_id"],
                good_id,
                platform if platform in {"android", "ios"} else account["platform"],
                origin="admin",
                created_by=admin_name(request),
                evaluate_voucher=False,
                replace_pending=False,
            )
            if not order:
                continue
            created += 1
            if note:
                with connect() as conn:
                    conn.execute(
                        "UPDATE orders SET admin_note=? WHERE order_id=?",
                        (note, order["order_id"]),
                    )
            if mode == "approve":
                payments.admin_approve_order(order["order_id"], actor=admin_name(request))
            elif mode == "reject":
                payments.admin_reject_order(order["order_id"], actor=admin_name(request), reason=note)
    _flash(request, f"已创建 {created} 个订单")
    return RedirectResponse("/admin/orders", status_code=303)


@router.post("/orders/bulk")
async def orders_bulk(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    order_ids = [str(value) for value in form.getlist("order_ids") if value]
    action = str(form.get("action") or "")
    reason = str(form.get("reason") or "").strip()
    changed = 0
    for order_id in order_ids:
        if action == "approve":
            result = payments.admin_approve_order(order_id, actor=admin_name(request))
        elif action == "reject":
            result = payments.admin_reject_order(order_id, actor=admin_name(request), reason=reason)
        elif action == "pending":
            result = payments.admin_set_pending(order_id, note=reason)
        else:
            result = {"result": 2}
        if result.get("result") == 0:
            changed += 1
    _flash(request, f"已处理 {changed} / {len(order_ids)} 个订单")
    return RedirectResponse("/admin/orders", status_code=303)


@router.post("/orders/{order_id}/action")
async def order_action(order_id: str, request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    action = str(form.get("action") or "")
    reason = str(form.get("reason") or "").strip()
    if action == "approve":
        result = payments.admin_approve_order(order_id, actor=admin_name(request))
    elif action == "reject":
        result = payments.admin_reject_order(order_id, actor=admin_name(request), reason=reason)
    else:
        result = payments.admin_set_pending(order_id, note=reason)
    _flash(request, "订单已更新" if result.get("result") == 0 else f"操作失败：{result.get('error', 'unknown')}")
    return RedirectResponse("/admin/orders", status_code=303)


def _parse_local_dt(value):
    value = str(value or "").strip()
    if not value:
        return 0
    try:
        import time as _time

        return int(_time.mktime(_time.strptime(value, "%Y-%m-%dT%H:%M")))
    except Exception:
        return 0


@router.get("/vouchers", response_class=HTMLResponse)
async def vouchers_page(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    with connect() as conn:
        accounts = conn.execute("SELECT * FROM accounts ORDER BY uid").fetchall()
    voucher_rows = vouchers.list_vouchers()
    for row in voucher_rows:
        row["scope_text"] = vouchers.scope_summary(row.get("scope") or {})
    return templates.TemplateResponse(
        request,
        "vouchers.html",
        {
            "admin": admin_name(request),
            "active": "vouchers",
            "vouchers": voucher_rows,
            "accounts": accounts,
            "products": sorted(payments.products(), key=lambda item: (item.get("name") or "", item.get("product_id") or "")),
            "message": _take_flash(request),
        },
    )


@router.post("/vouchers")
async def voucher_create(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    name = str(form.get("name") or "").strip() or "代金券"
    code = str(form.get("code") or "").strip()
    account_scope = str(form.get("account_scope") or "one")
    uid = _int(form.get("uid"), 0)
    scope_type = str(form.get("scope_type") or "all")
    goods = [str(value) for value in form.getlist("goods") if value]
    categories = [str(value) for value in form.getlist("categories") if value]
    platform = str(form.get("platform") or "")
    max_amount = _int(form.get("max_amount_yuan"), 0) * 100
    uses = _int(form.get("uses"), 1)
    if uses == 0:
        uses = -1
    start_at = _parse_local_dt(form.get("start_at"))
    end_at = _parse_local_dt(form.get("end_at"))
    scope = {
        "goods": goods if scope_type in {"goods", "mixed"} else [],
        "categories": categories if scope_type in {"categories", "mixed"} else (["all"] if scope_type == "all" else []),
        "platform": platform if platform in {"android", "ios"} else "any",
    }
    if scope_type in {"categories", "mixed"} and not categories:
        _flash(request, "选择商品类别范围时至少要勾选一个类别")
        return RedirectResponse("/admin/vouchers", status_code=303)
    if scope_type in {"goods", "mixed"} and not goods:
        _flash(request, "选择指定商品范围时至少要选择一个商品")
        return RedirectResponse("/admin/vouchers", status_code=303)
    with connect() as conn:
        if account_scope == "all":
            accounts = conn.execute("SELECT * FROM accounts ORDER BY uid").fetchall()
        else:
            accounts = conn.execute("SELECT * FROM accounts WHERE uid=?", (uid,)).fetchall()
    if not accounts:
        _flash(request, "没有找到适用账号")
        return RedirectResponse("/admin/vouchers", status_code=303)
    created = 0
    for account in accounts:
        voucher_code = code or vouchers.new_code()
        if len(accounts) > 1 and code:
            voucher_code = f"{code}-{account['uid']}"
        try:
            vouchers.create_voucher(
                name=name,
                code=voucher_code,
                account_id=account["id"],
                account_uid=account["uid"],
                scope=scope,
                max_amount=max_amount,
                uses=uses,
                start_at=start_at,
                end_at=end_at,
            )
            created += 1
        except sqlite3.IntegrityError:
            continue
    _flash(request, f"已创建 {created} 张代金券")
    return RedirectResponse("/admin/vouchers", status_code=303)


@router.post("/vouchers/{voucher_id}/status")
async def voucher_status(voucher_id: int, request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    vouchers.set_status(voucher_id, 1 if str(form.get("status")) == "1" else 0)
    return RedirectResponse("/admin/vouchers", status_code=303)


@router.post("/vouchers/{voucher_id}/delete")
async def voucher_delete(voucher_id: int, request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    vouchers.delete_voucher(voucher_id)
    return RedirectResponse("/admin/vouchers", status_code=303)


@router.get("/gifts", response_class=HTMLResponse)
async def gifts_page(request: Request, q: str = ""):
    redirect = need_login(request)
    if redirect:
        return redirect
    needle = q.strip().lower()
    rows = []
    for row in gift_catalog.catalog_rows():
        if needle and needle not in str(row["goodId"]).lower() and needle not in str(row["displayName"]).lower():
            continue
        enriched = dict(row)
        enriched["contents"] = gift_catalog.item_rows(row.get("items") or [])
        rows.append(enriched)
    cash_rows = []
    for row in sorted(payments.cash_goods().values(), key=lambda item: int(item.get("price") or 0)):
        base = int(row.get("diamondNum") or 0)
        plus = int(row.get("plusNum") or 0)
        cash_rows.append(
            {
                "goodId": row.get("goodId"),
                "price": int(row.get("price") or 0),
                "base": base,
                "plus": plus,
                "normal": base + plus,
                "first": int(row.get("doubleCount") or 0),
            }
        )
    return templates.TemplateResponse(
        request,
        "gifts.html",
        {
            "admin": admin_name(request),
            "active": "gifts",
            "rows": rows,
            "query": q,
            "total": len(gift_catalog.catalog_rows()),
            "current_count": len(gift_catalog.current_goods()),
            "cash_rows": cash_rows,
        },
    )


@router.get("/api/resources")
async def resource_search_api(request: Request, q: str = "", limit: int = 80):
    redirect = need_login(request)
    if redirect:
        return JSONResponse({"error": "login required"}, status_code=401)
    return {"items": game_data.resource_search(q, limit=min(max(limit, 1), 200))}


@router.post("/mail/templates")
async def mail_template_save(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    raw_items = str(form.get("items") or "[]")
    try:
        items = json.loads(raw_items)
    except json.JSONDecodeError as exc:
        _flash(request, f"附件 JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列")
        return RedirectResponse("/admin/mail", status_code=303)
    result = mail_service.save_template(
        name=str(form.get("name") or "").strip(),
        sender=str(form.get("sender") or "penguin_logistics").strip(),
        subject=str(form.get("subject") or "").strip(),
        content=str(form.get("content") or ""),
        items=items,
        expire_days=_optional_int(form.get("expire_days")) or 365,
    )
    _flash(request, "模板已保存" if result.get("result") == 0 else result.get("error", "模板保存失败"))
    return RedirectResponse("/admin/mail", status_code=303)


@router.post("/mail/templates/{template_id}/delete")
async def mail_template_delete(request: Request, template_id: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    mail_service.delete_template(template_id)
    return RedirectResponse("/admin/mail", status_code=303)


@router.post("/mail/send")
async def mail_center_send(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        values = _mail_form_values(form)
    except ValueError as exc:
        _flash(request, str(exc))
        return RedirectResponse("/admin/mail", status_code=303)
    scope = str(form.get("scope") or "one")
    if scope == "all":
        uids = [int(account["uid"]) for account in list_accounts()]
    else:
        raw_uids = str(form.get("uids") or "").strip()
        uids = []
        if raw_uids:
            for value in raw_uids.replace(";", ",").split(","):
                uid = _int(value.strip(), 0)
                if uid and uid not in uids:
                    uids.append(uid)
        uid = _int(form.get("uid"), 0)
        if uid and uid not in uids:
            uids.append(uid)
    if not uids:
        _flash(request, "没有选择收件账号")
        return RedirectResponse("/admin/mail", status_code=303)
    result = mail_service.send_bulk(uids, **values)
    if result["sent"]:
        message = f"已发送 {len(result['sent'])} 封邮件"
        if result["errors"]:
            message += f"，失败 {len(result['errors'])} 个账号"
        _flash(request, message)
    else:
        _flash(request, "邮件发送失败")
    return RedirectResponse("/admin/mail", status_code=303)


@router.post("/mail/{mail_id}/delete")
async def mail_instance_delete(request: Request, mail_id: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    mail_service.delete_instance(mail_id)
    return RedirectResponse("/admin/mail", status_code=303)


@router.post("/accounts/{uid}/reset-token")
async def account_reset_token(request: Request, uid: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    with connect() as conn:
        conn.execute("UPDATE accounts SET token=? WHERE uid=?", (new_token("lab_"), uid))
    _flash(request, "Token 已重置，旧 Token 立即失效")
    return RedirectResponse(f"/admin/accounts/{uid}", status_code=303)


@router.post("/accounts/{uid}/delete")
async def account_delete(request: Request, uid: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    with connect() as conn:
        account = conn.execute("SELECT id FROM accounts WHERE uid=?", (uid,)).fetchone()
        if account:
            conn.execute("DELETE FROM battle_sessions WHERE account_id=?", (account["id"],))
            conn.execute("DELETE FROM orders WHERE account_id=?", (account["id"],))
        conn.execute("DELETE FROM sessions WHERE account_id IN (SELECT id FROM accounts WHERE uid=?)", (uid,))
        conn.execute("DELETE FROM mail_instances WHERE uid=?", (uid,))
        conn.execute("DELETE FROM accounts WHERE uid=?", (uid,))
    return RedirectResponse("/admin/accounts", status_code=303)


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request, "logs.html", {"admin": admin_name(request), "logs": recent_logs(500)}
    )


@router.get("/development", response_class=HTMLResponse)
async def development_page(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    status = await run_in_threadpool(selfcheck.collect_status, True)
    gate_done = sum(1 for gate in status["gates"] if gate["state"] == "done")
    return templates.TemplateResponse(
        request,
        "development.html",
        {
            "admin": admin_name(request),
            "status": status,
            "gate_done": gate_done,
            "message": _take_flash(request),
        },
    )


@router.post("/development/repair-adb")
async def development_repair_adb(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    result = await run_in_threadpool(selfcheck.repair_adb)
    _flash(request, "ADB 链路已修复" if result.get("ok") else "ADB 修复失败，请查看 watchdog 日志")
    return RedirectResponse("/admin/development", status_code=303)


@router.get("/development/adb.json")
async def development_adb_json(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    return JSONResponse(await run_in_threadpool(selfcheck.adb_diagnostics))


@router.get("/protocol", response_class=HTMLResponse)
async def protocol_page(request: Request, q: str = "", kind: str = "", limit: int = 160):
    redirect = need_login(request)
    if redirect:
        return redirect
    catalog = protocol_catalog.summary()
    routes, route_total = protocol_catalog.search_routes(q, kind, limit)
    types, type_total = protocol_catalog.search_types(q, limit=60)
    dump = await run_in_threadpool(il2cpp_dump.summary)
    return templates.TemplateResponse(
        request,
        "protocol.html",
        {
            "admin": admin_name(request),
            "catalog": catalog,
            "routes": routes,
            "route_total": route_total,
            "types": types,
            "type_total": type_total,
            "dump": dump,
            "q": q,
            "kind": kind,
            "limit": limit,
        },
    )


@router.get("/protocol/api")
async def protocol_api(request: Request, q: str = "", kind: str = "", limit: int = 200):
    redirect = need_login(request)
    if redirect:
        return redirect
    routes, total = protocol_catalog.search_routes(q, kind, limit)
    return JSONResponse(
        {
            "summary": protocol_catalog.summary()["counts"],
            "query": q,
            "kind": kind,
            "total": total,
            "routes": routes,
        }
    )


@router.get("/protocol/type", response_class=HTMLResponse)
async def protocol_type(request: Request, name: str = ""):
    redirect = need_login(request)
    if redirect:
        return redirect
    item = protocol_catalog.get_type(name)
    return templates.TemplateResponse(
        request,
        "protocol_type.html",
        {"admin": admin_name(request), "item": item, "name": name},
        status_code=200 if item else 404,
    )


@router.get("/protocol/type/api")
async def protocol_type_api(request: Request, name: str = ""):
    redirect = need_login(request)
    if redirect:
        return redirect
    item = protocol_catalog.get_type(name)
    if not item:
        return JSONResponse({"error": "type not found", "name": name}, status_code=404)
    return JSONResponse(item)


@router.get("/protocol/dump", response_class=HTMLResponse)
async def protocol_dump_page(request: Request, q: str = "", limit: int = 80):
    redirect = need_login(request)
    if redirect:
        return redirect
    summary = await run_in_threadpool(il2cpp_dump.summary)
    rows, total = ([], 0)
    if q.strip():
        rows, total = await run_in_threadpool(il2cpp_dump.search, q, limit)
    return templates.TemplateResponse(
        request,
        "protocol_dump.html",
        {"admin": admin_name(request), "summary": summary, "rows": rows, "total": total, "q": q},
    )


@router.get("/protocol/dump/api")
async def protocol_dump_api(request: Request, q: str = "", limit: int = 80):
    redirect = need_login(request)
    if redirect:
        return redirect
    summary = await run_in_threadpool(il2cpp_dump.summary)
    rows, total = ([], 0)
    if q.strip():
        rows, total = await run_in_threadpool(il2cpp_dump.search, q, limit)
    return JSONResponse(
        {
            "summary": summary,
            "query": q,
            "total": total,
            "results": rows,
        }
    )


@router.get("/protocol/dump/type", response_class=HTMLResponse)
async def protocol_dump_type(request: Request, name: str = ""):
    redirect = need_login(request)
    if redirect:
        return redirect
    item = await run_in_threadpool(il2cpp_dump.get_type, name)
    return templates.TemplateResponse(
        request,
        "protocol_dump_type.html",
        {"admin": admin_name(request), "item": item, "name": name},
        status_code=200 if item else 404,
    )


@router.get("/protocol/dump/file")
async def protocol_dump_file(request: Request, kind: str = "dump"):
    redirect = need_login(request)
    if redirect:
        return redirect
    summary = await run_in_threadpool(il2cpp_dump.summary)
    if not summary.get("exists"):
        return JSONResponse({"error": "dump.cs not found"}, status_code=404)
    if kind == "stringliteral":
        path = il2cpp_dump.dump_path().parent / "stringliteral.json"
        media_type = "application/json"
        filename = "stringliteral.json"
    elif kind == "script":
        path = il2cpp_dump.dump_path().parent / "script.json"
        media_type = "application/json"
        filename = "script.json"
    elif kind == "header":
        path = il2cpp_dump.dump_path().parent / "il2cpp.h"
        media_type = "text/plain; charset=utf-8"
        filename = "il2cpp.h"
    else:
        path = il2cpp_dump.dump_path()
        media_type = "text/plain; charset=utf-8"
        filename = "dump.cs"
    if not path.exists():
        return JSONResponse({"error": f"{filename} not found"}, status_code=404)
    return FileResponse(path, media_type=media_type, filename=filename)


@router.post("/protocol/rebuild")
async def protocol_rebuild(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    result = await run_in_threadpool(protocol_catalog.rebuild_from_memory_dump)
    return RedirectResponse(f"/admin/protocol?rebuilt={'1' if result.get('ok') else '0'}", status_code=303)


@router.post("/development/run-tests")
async def development_run_tests(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    if not selfcheck.protocol_tests_running():
        asyncio.create_task(run_in_threadpool(selfcheck.run_protocol_tests))
    return RedirectResponse("/admin/development", status_code=303)


@router.get("/development/catalog.json")
async def development_catalog(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    path = selfcheck.artifact_path("protocol_catalog")
    if not path:
        return JSONResponse({"error": "protocol_catalog.json not found"}, status_code=404)
    return FileResponse(path, media_type="application/json", filename="protocol_catalog.json")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    settings = runtime_settings.values()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "admin": admin_name(request),
            "settings": settings,
            "server_name": settings["server_name"],
            "announcement": settings["announcement"],
            "client_version": settings["client_version"],
            "res_version": settings["res_version"],
            "asset_status": _asset_diagnostics(),
            "message": _take_flash(request),
        },
    )


@router.post("/settings")
async def settings_save(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    runtime_settings.save(
        {
            "server_name": str(form.get("server_name") or "").strip() or SERVER_NAME,
            "announcement": str(form.get("announcement") or "").strip(),
            "client_version": str(form.get("client_version") or "").strip() or CLIENT_VERSION,
            "res_version": str(form.get("res_version") or "").strip() or RES_VERSION,
            "force_update": 1 if form.get("force_update") == "1" else 0,
            "force_launcher_update": 1 if form.get("force_launcher_update") == "1" else 0,
            "force_resource_update": 1 if form.get("force_resource_update") == "1" else 0,
            "maintenance": 1 if form.get("maintenance") == "1" else 0,
            "maintenance_message": str(form.get("maintenance_message") or "").strip(),
            "asset_base_url": str(form.get("asset_base_url") or "").strip() or ASSET_CDN,
            "asset_fallback_url": str(form.get("asset_fallback_url") or "").strip() or ASSET_CDN,
            "asset_root": str(form.get("asset_root") or "").strip(),
            "update_url": str(form.get("update_url") or "").strip(),
            "update_notes": str(form.get("update_notes") or "").strip(),
        }
    )
    _flash(request, "服务设置已保存")
    return RedirectResponse("/admin/settings", status_code=303)


def _safe_relative_path(value):
    candidate = Path(str(value or "").replace("\\", "/"))
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("资源路径必须是安全的相对路径")
    return candidate


@router.post("/settings/assets/upload")
async def settings_assets_upload(
    request: Request,
    upload: UploadFile = File(...),
    prefix: str = Form(""),
):
    redirect = need_login(request)
    if redirect:
        return redirect
    settings = runtime_settings.values()
    root_text = str(settings.get("asset_root") or "").strip()
    if not root_text:
        _flash(request, "请先填写并保存资源根目录")
        return RedirectResponse("/admin/settings", status_code=303)
    root = Path(root_text).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        prefix_path = _safe_relative_path(prefix) if str(prefix or "").strip() else Path()
    except ValueError as exc:
        _flash(request, str(exc))
        return RedirectResponse("/admin/settings", status_code=303)
    filename = Path(upload.filename or "resource.bin")
    if filename.name.lower().endswith(".zip"):
        extracted = 0
        try:
            upload.file.seek(0)
            with zipfile.ZipFile(upload.file) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    try:
                        relative = _safe_relative_path(info.filename)
                    except ValueError:
                        continue
                    target = (root / prefix_path / relative).resolve()
                    try:
                        target.relative_to(root)
                    except ValueError:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    extracted += 1
            _flash(request, f"资源包已解压并下发：{extracted} 个文件")
        except (zipfile.BadZipFile, OSError) as exc:
            _flash(request, f"资源包处理失败：{exc}")
            return RedirectResponse("/admin/settings", status_code=303)
    else:
        target = (root / prefix_path / filename.name).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            _flash(request, "资源文件名不安全")
            return RedirectResponse("/admin/settings", status_code=303)
        target.parent.mkdir(parents=True, exist_ok=True)
        upload.file.seek(0)
        with target.open("wb") as output:
            shutil.copyfileobj(upload.file, output)
        _flash(request, f"资源已保存到 {target.relative_to(root)}")
    return RedirectResponse("/admin/settings", status_code=303)

