import asyncio
import copy
import json
import re
import shutil
import sqlite3
import time
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
    battles,
    gacha_service,
    game_data,
    gift_catalog,
    grants,
    mail_service,
    missions,
    payments,
    product_catalog,
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


def _format_ts(value):
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    import time as _time

    return _time.strftime("%Y-%m-%dT%H:%M", _time.localtime(value))


def _parse_ts(value):
    text = str(value or "").strip()
    if not text:
        return 0
    import time as _time

    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(_time.mktime(_time.strptime(text, fmt)))
        except Exception:
            continue
    return 0


@router.get("/products", response_class=HTMLResponse)
async def products_page(request: Request, q: str = "", page: int = 1):
    redirect = need_login(request)
    if redirect:
        return redirect
    rows = product_catalog.all_products(include_disabled=True)
    needle = str(q or "").strip().lower()
    if needle:
        rows = [
            row
            for row in rows
            if needle
            in " ".join(
                str(row.get(key) or "")
                for key in ("product_id", "appstore_id", "name", "store_id", "desc")
            ).lower()
        ]
    page_size = 80
    page = max(1, int(page or 1))
    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    rows = rows[(page - 1) * page_size : page * page_size]
    for row in rows:
        row["price_yuan"] = f"{_int(row.get('price'), 0) / 100:.2f}"
    return templates.TemplateResponse(
        request,
        "products.html",
        {
            "admin": admin_name(request),
            "active": "products",
            "products": rows,
            "total": total,
            "page": page,
            "pages": pages,
            "q": q,
            "message": _take_flash(request),
        },
    )


@router.post("/products/create")
async def product_create(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        product = product_catalog.save_product(
            {
                "product_id": form.get("product_id"),
                "appstore_id": form.get("appstore_id"),
                "store_id": form.get("store_id"),
                "name": form.get("name"),
                "desc": form.get("desc"),
                "price": form.get("price"),
                "platform": form.get("platform"),
                "status": form.get("status") or "1",
            }
        )
        _flash(request, f"已创建商品 {product['product_id']}")
    except ValueError as exc:
        _flash(request, str(exc))
    return RedirectResponse("/admin/products", status_code=303)


@router.get("/products/{product_id}", response_class=HTMLResponse)
async def product_edit_page(product_id: str, request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    product = product_catalog.find_product(good_id=product_id, include_disabled=True)
    if not product:
        _flash(request, "商品不存在")
        return RedirectResponse("/admin/products", status_code=303)
    items = product.get("grant_items")
    if not isinstance(items, list) or not items:
        source = str(product.get("grant_good_id") or "")
        items = payments.good_items(source) if source else []
    initial = []
    catalog = game_data.resource_catalog()
    for item in items or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        meta = catalog.get(str(item.get("id"))) or {}
        initial.append(
            {
                "id": str(item.get("id")),
                "type": str(item.get("type") or meta.get("type") or "MATERIAL"),
                "count": _int(item.get("count"), 1),
                "name": meta.get("name") or str(item.get("id")),
            }
        )
    return templates.TemplateResponse(
        request,
        "product_edit.html",
        {
            "admin": admin_name(request),
            "active": "products",
            "product": product,
            "grant_items": initial,
            "message": _take_flash(request),
        },
    )


@router.post("/products/{product_id}/content")
async def product_content_update(product_id: str, request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    product = product_catalog.find_product(good_id=product_id, include_disabled=True)
    if not product:
        _flash(request, "商品不存在")
        return RedirectResponse("/admin/products", status_code=303)
    form = await request.form()
    catalog = game_data.resource_catalog()
    ids = [str(value).strip() for value in form.getlist("item_id") if str(value).strip()]
    counts = [str(value) for value in form.getlist("item_count")]
    items = []
    try:
        for index, item_id in enumerate(ids):
            meta = catalog.get(item_id)
            if not meta:
                raise ValueError(f"不存在的资源 ID：{item_id}")
            count = _int(counts[index] if index < len(counts) else 1, 0)
            if count <= 0 or count > 999999:
                raise ValueError(f"资源数量必须在 1-999999 之间：{item_id}")
            items.append({"id": item_id, "type": meta["type"], "count": count})
        values = dict(product)
        values["grant_items"] = items
        if items:
            values["grant_good_id"] = ""
        product_catalog.save_product(values, original_id=product_id)
        _flash(request, f"商品 {product_id} 内容已保存（{len(items)} 种）")
    except ValueError as exc:
        _flash(request, str(exc))
    return RedirectResponse(f"/admin/products/{product_id}", status_code=303)


@router.post("/products/{product_id}/update")
async def product_update(product_id: str, request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        product = product_catalog.save_product(
            {
                "product_id": product_id,
                "appstore_id": form.get("appstore_id"),
                "store_id": form.get("store_id"),
                "name": form.get("name"),
                "desc": form.get("desc"),
                "price": form.get("price"),
                "platform": form.get("platform"),
                "status": form.get("status") or "0",
            },
            original_id=product_id,
        )
        _flash(request, f"已保存商品 {product['product_id']}")
    except ValueError as exc:
        _flash(request, str(exc))
    return RedirectResponse("/admin/products", status_code=303)


@router.post("/products/{product_id}/status")
async def product_status(product_id: str, request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    product_catalog.set_status(product_id, str(form.get("status")) == "1")
    _flash(request, f"商品 {product_id} 状态已更新")
    return RedirectResponse("/admin/products", status_code=303)


@router.post("/products/{product_id}/delete")
async def product_delete(product_id: str, request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    product_catalog.delete_product(product_id)
    _flash(request, f"已下架商品 {product_id}")
    return RedirectResponse("/admin/products", status_code=303)


def _gacha_rows(q="", status=""):
    rows = gacha_service.all_pools()
    now = int(__import__("time").time())
    needle = str(q or "").strip().lower()
    result = []
    for row in rows:
        pool_id = str(row.get("gachaPoolId") or "")
        name = str(row.get("gachaPoolName") or pool_id)
        if needle and needle not in f"{pool_id} {name}".lower():
            continue
        start = _int(row.get("openTime"), 0)
        end = _int(row.get("endTime"), 0)
        active = bool(row.get("enabled", True)) and (start <= now if start else True) and (end >= now if end else True)
        base = gacha_service.base_pool(pool_id)
        client_start = _int(base.get("openTime"), 0)
        client_end = _int(base.get("endTime"), 0)
        client_open = bool(base) and (client_start <= now if client_start else True) and (client_end >= now if client_end else True)
        if status == "active" and not active:
            continue
        if status == "disabled" and row.get("enabled", True):
            continue
        result.append(
            {
                **row,
                "pool_id": pool_id,
                "name": name,
                "open_text": _format_ts(start),
                "end_text": _format_ts(end),
                "is_active": active,
                "client_visible": bool(base),
                "client_open": client_open,
                "client_start_text": _format_ts(client_start),
                "client_end_text": _format_ts(client_end),
                "rate6": _float(row.get("rate6"), row.get("rate6") if row.get("rate6") is not None else ""),
            }
        )
    result.sort(key=lambda row: (not row["is_active"], row.get("open_text") or "", row["pool_id"]))
    return result


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@router.get("/gacha", response_class=HTMLResponse)
async def gacha_page(request: Request, q: str = "", status: str = "", page: int = 1):
    redirect = need_login(request)
    if redirect:
        return redirect
    rows = _gacha_rows(q=q, status=status)
    open_count = sum(1 for row in _gacha_rows() if row["is_active"] and row["client_open"])
    page_size = 80
    page = max(1, int(page or 1))
    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    rows = rows[(page - 1) * page_size : page * page_size]
    return templates.TemplateResponse(
        request,
        "gacha.html",
        {
            "admin": admin_name(request),
            "active": "gacha",
            "pools": rows,
            "total": total,
            "open_count": open_count,
            "page": page,
            "pages": pages,
            "q": q,
            "status_filter": status,
            "message": _take_flash(request),
        },
    )


GACHA_RARITY = {
    5: {"label": "6★", "tier": "TIER_6", "avail": "avail6", "up": "up6", "rate": "rate6", "up_rate": "upRate"},
    4: {"label": "5★", "tier": "TIER_5", "avail": "avail5", "up": "up5", "rate": "rate5", "up_rate": "up5Rate"},
    3: {"label": "4★", "tier": "TIER_4", "avail": "avail4", "up": "", "rate": "rate4", "up_rate": ""},
    2: {"label": "3★", "tier": "TIER_3", "avail": "avail3", "up": "", "rate": "rate3", "up_rate": ""},
}
GACHA_TIER_RANK = {spec["tier"]: rank for rank, spec in GACHA_RARITY.items()}


def _gacha_character_catalog():
    return {
        char_id: item
        for char_id, item in game_data.character_catalog().items()
        if char_id.startswith("char_") and str(item.get("rarity") or "") in GACHA_TIER_RANK
    }


def _tier_label(tier):
    rank = GACHA_TIER_RANK.get(str(tier or ""))
    return GACHA_RARITY[rank]["label"] if rank else str(tier or "未知")


def _split_character_tokens(value):
    return [token.strip() for token in re.split(r"[,，;；\n\r\t]+", str(value or "")) if token.strip()]


def _resolve_character_ids(value, rarity, catalog):
    spec = GACHA_RARITY[rarity]
    resolved = []
    for token in _split_character_tokens(value):
        char_id = token if token in catalog else ""
        matches = []
        if not char_id:
            needle = token.lower()
            matches = [
                candidate_id
                for candidate_id, item in catalog.items()
                if needle in {
                    str(item.get("name") or "").lower(),
                    str(item.get("appellation") or "").lower(),
                }
            ]
            if not matches:
                matches = [
                    candidate_id
                    for candidate_id, item in catalog.items()
                    if needle and needle in str(item.get("name") or "").lower()
                ]
            if not matches:
                raise ValueError(f"找不到干员：{token}")
            if len(matches) > 1:
                sample = "、".join(matches[:6])
                raise ValueError(f"干员名称不唯一：{token}（{sample}）")
            char_id = matches[0]
        item = catalog.get(char_id) or {}
        actual_tier = str(item.get("rarity") or "")
        if actual_tier != spec["tier"]:
            raise ValueError(
                f"{token}（{char_id}）是 {_tier_label(actual_tier)}，不能放进 {spec['label']} 奖池"
            )
        if char_id not in resolved:
            resolved.append(char_id)
    return resolved


def _character_options():
    catalog = _gacha_character_catalog()
    options = {str(rank): [] for rank in GACHA_RARITY}
    names = {}
    for char_id, item in catalog.items():
        rank = GACHA_TIER_RANK.get(str(item.get("rarity") or ""))
        if rank is None:
            continue
        row = {"id": char_id, "name": str(item.get("name") or char_id)}
        options[str(rank)].append(row)
        names[char_id] = row["name"]
    for rows in options.values():
        rows.sort(key=lambda row: (row["name"], row["id"]))
    return options, names


def _gacha_edit_context(pool_id):
    meta = gacha_service.pool_meta(pool_id)
    if not meta:
        return None
    detail = gacha_service.pool_detail(pool_id) or {}
    info = detail.get("detailInfo") or {}
    avail = ((info.get("availCharInfo") or {}).get("perAvailList")) or []
    rates = {5: 0.02, 4: 0.08, 3: 0.5, 2: 0.4}
    avail_by_rank = {rank: [] for rank in GACHA_RARITY}
    for row in avail:
        rank = _int(row.get("rarityRank"), 0)
        rates[rank] = _float(row.get("totalPercent"), 0)
        if rank in avail_by_rank:
            avail_by_rank[rank] = [str(value) for value in row.get("charIdList") or []]
    up_by_rank = {5: [], 4: []}
    for row in ((info.get("upCharInfo") or {}).get("perCharList") or []):
        rank = _int(row.get("rarityRank"), 0)
        if rank in up_by_rank:
            up_by_rank[rank] = [str(value) for value in row.get("charIdList") or []]
    options, names = _character_options()
    meta["open_text"] = _format_ts(meta.get("openTime"))
    meta["end_text"] = _format_ts(meta.get("endTime"))
    base = gacha_service.base_pool(pool_id)
    client_start = _int(base.get("openTime"), 0)
    client_end = _int(base.get("endTime"), 0)
    now = int(__import__("time").time())
    meta["client_visible"] = bool(base)
    meta["client_start_text"] = _format_ts(client_start)
    meta["client_end_text"] = _format_ts(client_end)
    meta["client_open"] = bool(base) and (client_start <= now if client_start else True) and (client_end >= now if client_end else True)
    meta["rate6_percent"] = round(rates.get(5, 0.02) * 100, 3)
    meta["rate5_percent"] = round(rates.get(4, 0.08) * 100, 3)
    meta["rate4_percent"] = round(rates.get(3, 0.5) * 100, 3)
    meta["rate3_percent"] = round(rates.get(2, 0.4) * 100, 3)
    meta["up_chars_text"] = ",".join(up_by_rank[5])
    meta["up5_chars_text"] = ",".join(up_by_rank[4])
    meta["up_rate6_percent"] = round(_float(meta.get("upRate"), 0.5) * 100, 1)
    meta["up_rate5_percent"] = round(_float(meta.get("up5Rate"), _float(meta.get("upRate"), 0.5)) * 100, 1)
    meta["avail_text"] = {str(rank): "\n".join(avail_by_rank[rank]) for rank in GACHA_RARITY}
    meta["up_text"] = {str(rank): "\n".join(up_by_rank.get(rank, [])) for rank in (5, 4)}
    meta["avail_counts"] = {str(rank): len(avail_by_rank[rank]) for rank in GACHA_RARITY}
    meta["up_counts"] = {str(rank): len(up_by_rank.get(rank, [])) for rank in (5, 4)}
    meta["char_options"] = options
    meta["char_names"] = names
    meta["avail_preview"] = {
        str(rank): [{"id": char_id, "name": names.get(char_id, char_id)} for char_id in avail_by_rank[rank]]
        for rank in GACHA_RARITY
    }
    meta["up_preview"] = {
        str(rank): [{"id": char_id, "name": names.get(char_id, char_id)} for char_id in up_by_rank[rank]]
        for rank in (5, 4)
    }
    return meta


@router.get("/gacha/{pool_id}", response_class=HTMLResponse)
async def gacha_edit_page(pool_id: str, request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    meta = _gacha_edit_context(pool_id)
    if not meta:
        _flash(request, "卡池不存在")
        return RedirectResponse("/admin/gacha", status_code=303)
    return templates.TemplateResponse(
        request,
        "gacha_edit.html",
        {
            "admin": admin_name(request),
            "active": "gacha",
            "pool": meta,
            "message": _take_flash(request),
        },
    )


def _gacha_form_values(form, pool_id):
    exists = bool(gacha_service.pool_meta(pool_id))
    base_pool = str(form.get("base_pool") or "").strip()
    reference_id = pool_id if exists else base_pool
    reference_detail = (
        gacha_service.pool_detail(reference_id)
        if reference_id and gacha_service.pool_meta(reference_id)
        else {}
    )
    reference_up = {5: [], 4: []}
    reference_up_rate = {5: 0.5, 4: 0.5}
    for row in ((reference_detail.get("detailInfo") or {}).get("upCharInfo") or {}).get("perCharList") or []:
        rank = _int(row.get("rarityRank"), 0)
        if rank in reference_up:
            reference_up[rank] = [str(value) for value in row.get("charIdList") or []]
            reference_up_rate[rank] = max(0.0, min(1.0, _float(row.get("percent"), 0.5)))

    def parse_chars(value):
        return [
            item.strip()
            for item in str(value or "").replace("\n", ",").split(",")
            if item.strip()
        ]

    raw_up_chars = form.get("up_chars")
    up_chars = (
        reference_up[5]
        if raw_up_chars is None or (not exists and not str(raw_up_chars).strip())
        else parse_chars(raw_up_chars)
    )
    raw_up_rate = form.get("up_rate_percent")
    up_rate = (
        reference_up_rate[5]
        if raw_up_rate is None or (not exists and not str(raw_up_rate).strip())
        else max(0.0, min(1.0, _float(raw_up_rate, 50.0) / 100.0))
    )
    raw_up5_chars = form.get("up5_chars")
    up5_chars = reference_up[4] if raw_up5_chars is None else parse_chars(raw_up5_chars)
    raw_up5_rate = form.get("up5_rate_percent")
    up5_rate = (
        reference_up_rate[4]
        if raw_up5_rate is None
        else max(0.0, min(1.0, _float(raw_up5_rate, 50.0) / 100.0))
    )
    values = {
        "gachaPoolName": str(form.get("name") or pool_id),
        "enabled": str(form.get("enabled") or "1") == "1",
        "openTime": _parse_ts(form.get("open_time")),
        "endTime": _parse_ts(form.get("end_time")),
        "singlePrice": max(0, _int(form.get("single_price"), 600)),
        "tenPrice": max(0, _int(form.get("ten_price"), 6000)),
        "upChars": up_chars,
        "upRate": up_rate,
        "up5Chars": up5_chars,
        "up5Rate": up5_rate,
        "rate6": max(0.0, _float(form.get("rate6_percent"), 2.0) / 100.0),
        "rate5": max(0.0, _float(form.get("rate5_percent"), 8.0) / 100.0),
        "rate4": max(0.0, _float(form.get("rate4_percent"), 50.0) / 100.0),
        "rate3": max(0.0, _float(form.get("rate3_percent"), 40.0) / 100.0),
        "guarantee5Avail": 1 if str(form.get("guarantee5")) == "1" else 0,
        "guarantee5Count": max(1, _int(form.get("guarantee5_count"), 10)),
        "basePool": base_pool,
    }
    rate_total = values["rate6"] + values["rate5"] + values["rate4"] + values["rate3"]
    if abs(rate_total - 1.0) > 0.0001:
        raise ValueError(f"四项概率合计必须为 100%，当前为 {rate_total * 100:.3f}%")
    return values


def _save_gacha_prize_pool(pool_id, form):
    meta = gacha_service.pool_meta(pool_id)
    if not meta:
        raise ValueError("卡池不存在")
    catalog = _gacha_character_catalog()
    avail = {
        rank: _resolve_character_ids(form.get(spec["avail"]), rank, catalog)
        for rank, spec in GACHA_RARITY.items()
    }
    rates = {
        5: _float(meta.get("rate6"), 0.02),
        4: _float(meta.get("rate5"), 0.08),
        3: _float(meta.get("rate4"), 0.5),
        2: _float(meta.get("rate3"), 0.4),
    }
    for rank, spec in GACHA_RARITY.items():
        if rates[rank] > 0 and not avail[rank]:
            raise ValueError(f"{spec['label']} 概率大于 0，奖池不能为空")
    up = {
        rank: _resolve_character_ids(form.get(GACHA_RARITY[rank]["up"]), rank, catalog)
        for rank in (5, 4)
    }
    for rank in (5, 4):
        for char_id in up[rank]:
            if char_id not in avail[rank]:
                avail[rank].append(char_id)
    up_rate6 = max(0.0, min(100.0, _float(form.get("up6_rate_percent"), _float(meta.get("upRate"), 0.5) * 100))) / 100.0
    up_rate5 = max(0.0, min(100.0, _float(form.get("up5_rate_percent"), _float(meta.get("up5Rate", meta.get("upRate")), 0.5) * 100))) / 100.0
    detail = copy.deepcopy(gacha_service.pool_detail(pool_id) or {})
    info = detail.setdefault("detailInfo", {})
    info["availCharInfo"] = {
        "perAvailList": [
            {
                "rarityRank": rank,
                "charIdList": avail[rank],
                "totalPercent": rates[rank],
            }
            for rank, _ in GACHA_RARITY.items()
        ]
    }
    up_rows = []
    if up[5]:
        up_rows.append({"rarityRank": 5, "charIdList": up[5], "percent": up_rate6})
    if up[4]:
        up_rows.append({"rarityRank": 4, "charIdList": up[4], "percent": up_rate5})
    info["upCharInfo"] = {"perCharList": up_rows} if up_rows else None
    detail["hasRateUp"] = bool(up_rows)
    detail.setdefault("gachaObjGroupType", 0)
    detail.setdefault("playerDataDelta", {"modified": {}, "deleted": {}})
    gacha_service.save_pool(
        pool_id,
        {
            "detail": detail,
            "upChars": up[5],
            "upRate": up_rate6,
            "up5Chars": up[4],
            "up5Rate": up_rate5,
        },
    )
    return avail, up


@router.post("/gacha/create")
async def gacha_create(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    pool_id = str(form.get("pool_id") or "").strip()
    try:
        base = str(form.get("base_pool") or "").strip()
        if base and not gacha_service.pool_meta(base):
            raise ValueError(f"基础卡池不存在：{base}")
        values = _gacha_form_values(form, pool_id)
        gacha_service.save_pool(pool_id, values, create=True)
        _flash(request, f"已上线新卡池 {pool_id}")
        return RedirectResponse(f"/admin/gacha/{pool_id}", status_code=303)
    except ValueError as exc:
        _flash(request, str(exc))
        return RedirectResponse("/admin/gacha", status_code=303)


@router.post("/gacha/{pool_id}/update")
async def gacha_update(pool_id: str, request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        values = _gacha_form_values(form, pool_id)
        gacha_service.save_pool(pool_id, values)
        _flash(request, f"卡池 {pool_id} 已保存")
    except ValueError as exc:
        _flash(request, str(exc))
    return RedirectResponse(f"/admin/gacha/{pool_id}", status_code=303)


@router.post("/gacha/{pool_id}/pool")
async def gacha_pool_update(pool_id: str, request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    form = await request.form()
    try:
        avail, up = _save_gacha_prize_pool(pool_id, form)
        _flash(
            request,
            "奖池已保存：6★ {0} / 5★ {1} / 4★ {2} / 3★ {3}；UP 6★ {4} / 5★ {5}".format(
                len(avail[5]), len(avail[4]), len(avail[3]), len(avail[2]), len(up[5]), len(up[4])
            ),
        )
    except ValueError as exc:
        _flash(request, str(exc))
    return RedirectResponse(f"/admin/gacha/{pool_id}", status_code=303)


@router.post("/gacha/{pool_id}/delete")
async def gacha_delete(pool_id: str, request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    gacha_service.delete_pool(pool_id)
    _flash(request, f"卡池 {pool_id} 已下架")
    return RedirectResponse("/admin/gacha", status_code=303)


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
        phases = meta.get("phases") or []
        phase = min(max(0, _int(char.get("evolvePhase"), 0)), max(0, len(phases) - 1))
        max_level = max(1, _int((phases[phase] or {}).get("maxLevel"), 1)) if phases else 90
        character_rows.append(
            {
                "instId": str(inst_id),
                "charId": char_id,
                "name": meta.get("name") or char_id,
                "rarity": meta.get("rarity") or "",
                "level": _int(char.get("level"), 1),
                "maxLevel": max_level,
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
    mission_rows = []
    mission_root = user.get("mission") or {}
    mission_order = {"MAIN": 0, "DAILY": 1, "WEEKLY": 2}
    for mission_type, bucket in (mission_root.get("missions") or {}).items():
        if mission_type not in mission_order or not isinstance(bucket, dict):
            continue
        for mission_id, node in bucket.items():
            if not isinstance(node, dict):
                continue
            entry = game_data.mission(mission_id) or {}
            progress = node.get("progress") or []
            first = progress[0] if progress and isinstance(progress[0], dict) else {}
            value = _int(first.get("value"), 0)
            target = max(1, _int(first.get("target"), 1))
            state = _int(node.get("state"), 0)
            mission_rows.append(
                {
                    "type": mission_type,
                    "id": str(mission_id),
                    "name": entry.get("description") or str(mission_id),
                    "state": state,
                    "value": value,
                    "target": target,
                    "claimable": state == 2 and value >= target,
                    "point": _int(entry.get("periodicalPoint"), 0),
                }
            )
    mission_rows.sort(
        key=lambda row: (
            mission_order.get(row["type"], 9),
            _int((game_data.mission(row["id"]) or {}).get("sortId"), 0),
            row["id"],
        )
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
        "mission_rows": mission_rows,
        "mainline_summary": battles.mainline_overview(user),
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


@router.get("/battles", response_class=HTMLResponse)
async def battles_page(request: Request, uid: str = "", stage: str = "", limit: str = "200"):
    redirect = need_login(request)
    if redirect:
        return redirect
    uid_value = _optional_int(uid)
    rows = battles.list_battle_records(uid=uid_value, stage_id=stage.strip(), limit=_int(limit, 200))
    records = []
    for row in rows:
        item = dict(row)
        try:
            payload = json.loads(item.get("payload_json") or "{}")
        except Exception:
            payload = {}
        item["payload_pretty"] = json.dumps(payload, ensure_ascii=False, indent=2)[:20000]
        try:
            raw = json.loads(item.get("raw_data") or "{}")
        except Exception:
            raw = {}
        item["raw_pretty"] = json.dumps(raw, ensure_ascii=False, indent=2)[:20000]
        item["start_text"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(item.get("started_at") or item.get("created_at") or 0)))
        item["end_text"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(item.get("finished_at") or item.get("created_at") or 0)))
        records.append(item)
    return templates.TemplateResponse(
        request,
        "battles.html",
        {
            "admin": admin_name(request),
            "active": "battles",
            "records": records,
            "uid": uid,
            "stage": stage,
            "limit": limit,
            "message": _take_flash(request),
        },
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
    mainline_target = str(form.get("mainlineTarget") or "").strip()
    if mainline_target:
        sync = battles.sync_mainline_progress(user, mainline_target)
        if sync.get("result") == 0:
            _flash(
                request,
                f"已通关至 {sync.get('stageId')}，终端当前关卡为 {sync.get('mainStageProgress')}，"
                f"共标记 {sync.get('marked')} 个关卡",
            )
        else:
            _flash(request, sync.get("error") or "主线进度同步失败")
    _save_user(uid, user)
    return RedirectResponse(f"/admin/accounts/{uid}", status_code=303)


@router.post("/accounts/{uid}/missions/reset")
async def account_missions_reset(
    request: Request,
    uid: int,
    mission_type: str = Form("DAILY"),
):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    mission_type = str(mission_type or "DAILY").upper()
    user = _user(account)
    root = missions.mission_root(user)
    if mission_type == "WEEKLY":
        root["lastWeeklyReset"] = 0
    else:
        mission_type = "DAILY"
        root["lastDailyReset"] = 0
    missions.ensure_mission_state(user)
    _save_user(uid, user)
    _flash(request, f"已按当前周期刷新 {mission_type} 任务")
    return RedirectResponse(f"/admin/accounts/{uid}#missions", status_code=303)


@router.post("/accounts/{uid}/missions/claim")
async def account_missions_claim(
    request: Request,
    uid: int,
    mission_type: str = Form("DAILY"),
):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    mission_type = str(mission_type or "DAILY").upper()
    user = _user(account)
    result = missions.auto_confirm(user, mission_type)
    _save_user(uid, user)
    claimed = len(result.get("claimed") or [])
    _flash(request, f"{mission_type} 任务领取完成：{claimed} 个")
    return RedirectResponse(f"/admin/accounts/{uid}#missions", status_code=303)


@router.post("/accounts/{uid}/missions/progress")
async def account_mission_progress(
    request: Request,
    uid: int,
    mission_id: str = Form(...),
    value: int = Form(0),
):
    redirect = need_login(request)
    if redirect:
        return redirect
    account = _account(uid)
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    mission_id = str(mission_id or "").strip()
    entry = game_data.mission(mission_id) or {}
    if not entry:
        _flash(request, f"任务不存在：{mission_id}")
        return RedirectResponse(f"/admin/accounts/{uid}#missions", status_code=303)
    user = _user(account)
    mission_type = str(entry.get("type") or "").upper()
    bucket = missions.mission_bucket(user, mission_type, create=True)
    node = bucket.get(mission_id)
    if not isinstance(node, dict):
        node = {"state": 2, "progress": []}
        bucket[mission_id] = node
    progress = node.get("progress")
    if not isinstance(progress, list) or not progress:
        progress = [{"value": 0, "target": missions._mission_target(entry)}]
        node["progress"] = progress
    first = progress[0]
    if not isinstance(first, dict):
        first = {"value": 0, "target": missions._mission_target(entry)}
        progress[0] = first
    target = max(1, _int(first.get("target"), 1))
    first["value"] = min(target, max(0, int(value)))
    if int(node.get("state") or 0) == 3:
        node["state"] = 2
    _save_user(uid, user)
    _flash(request, f"已写入 {mission_id} 进度 {first['value']} / {target}")
    return RedirectResponse(f"/admin/accounts/{uid}#missions", status_code=303)


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


def _levelup_operator(user, char, meta, target_level):
    """Apply the in-game EXP-card upgrade server-side.

    This is the admin fallback for clients whose level-up button does not send
    /charBuild/upgradeChar.  It consumes the same 2001-2004 cards and uses the
    same exp/gold curves as the game endpoint, stopping at target_level.
    """
    const = game_data.excel("gamedata_const.json") or {}
    rarity = _int(str(meta.get("rarity") or "TIER_1").replace("TIER_", ""), 1)
    index = max(0, 5 - rarity)
    exp_maps = const.get("characterExpMap") or []
    gold_maps = const.get("characterUpgradeCostMap") or []
    exp_curve = exp_maps[index] if index < len(exp_maps) else (exp_maps[-1] if exp_maps else [])
    gold_curve = gold_maps[index] if index < len(gold_maps) else (gold_maps[-1] if gold_maps else [])
    phases = meta.get("phases") or []
    phase = min(max(0, _int(char.get("evolvePhase"), 0)), max(0, len(phases) - 1))
    max_level = max(1, _int((phases[phase] or {}).get("maxLevel"), 1)) if phases else 90
    target = min(max(1, _int(target_level, max_level)), max_level)
    level = min(max(1, _int(char.get("level"), 1)), max_level)
    exp = max(0, _int(char.get("exp"), 0))
    status = user.setdefault("status", {})
    gold = _int(status.get("gold"), 0)
    inventory = user.setdefault("inventory", {})
    values = {"2001": 200, "2002": 400, "2003": 1000, "2004": 2000}
    used = {}
    while level < target:
        need = _int(exp_curve[level - 1] if level - 1 < len(exp_curve) else -1, -1)
        cost = _int(gold_curve[level - 1] if level - 1 < len(gold_curve) else -1, -1)
        if need < 0 or cost < 0:
            break
        if exp < need:
            consumed = False
            for item_id in ("2001", "2002", "2003", "2004"):
                if _int(inventory.get(item_id), 0) <= 0:
                    continue
                inventory[item_id] = _int(inventory.get(item_id), 0) - 1
                exp += values[item_id]
                used[item_id] = used.get(item_id, 0) + 1
                consumed = True
                break
            if not consumed:
                break
            continue
        if gold < cost:
            break
        exp -= need
        gold -= cost
        level += 1
    char["level"] = level
    char["exp"] = exp
    status["gold"] = gold
    return level, exp, used


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
        if str(form.get("action") or "") == "levelup":
            level, exp, used = _levelup_operator(user, char, meta, form.get("target_level"))
            _save_user(uid, user)
            detail = "，消耗 " + "、".join(f"{k}×{v}" for k, v in used.items()) if used else ""
            _flash(request, f"已按经验卡升级到 {level} 级（EXP {exp}）{detail}")
            return RedirectResponse(f"/admin/accounts/{uid}#operators", status_code=303)
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
            "products": sorted(product_catalog.active_products(), key=lambda item: (item.get("name") or "", item.get("product_id") or "")),
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
            "products": sorted(product_catalog.active_products(), key=lambda item: (item.get("name") or "", item.get("product_id") or "")),
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


@router.get("/api/tutorial-audit")
async def tutorial_audit_api(request: Request, uid: int = 0):
    redirect = need_login(request)
    if redirect:
        return JSONResponse({"error": "login required"}, status_code=401)
    account = _account(uid)
    if not account:
        return JSONResponse({"error": "account not found"}, status_code=404)
    user = _user(account)
    if battles.reconcile_tutorial_state(user):
        _save_user(uid, user)
    return {"uid": uid, **battles.tutorial_audit(user)}


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

