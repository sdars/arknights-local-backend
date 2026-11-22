import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import APP_DIR, SERVER_NAME, CLIENT_VERSION, RES_VERSION
from ..db import connect, get_setting, set_setting, recent_logs
from ..security import verify_password, new_token
from ..services import account_stats, list_accounts, create_account
from .. import selfcheck

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def admin_name(request: Request):
    return request.session.get("admin")


def need_login(request: Request):
    if not admin_name(request):
        return RedirectResponse("/admin/login", status_code=303)
    return None


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
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "admin": admin_name(request),
            "stats": stats,
            "accounts": accounts,
            "logs": logs,
            "server_name": get_setting("server_name", SERVER_NAME),
            "client_version": CLIENT_VERSION,
            "res_version": RES_VERSION,
            "development": development,
            "gate_done": gate_done,
        },
    )


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request):
    redirect = need_login(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "accounts.html",
        {"admin": admin_name(request), "accounts": list_accounts()},
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
    with connect() as conn:
        account = conn.execute("SELECT * FROM accounts WHERE uid=?", (uid,)).fetchone()
    if not account:
        return RedirectResponse("/admin/accounts", status_code=303)
    return templates.TemplateResponse(
        request, "account_edit.html", {"admin": admin_name(request), "account": account}
    )


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
    with connect() as conn:
        conn.execute(
            "UPDATE accounts SET phone=?,nickname=?,token=?,banned=? WHERE uid=?",
            (phone.strip(), nickname.strip(), token.strip(), 1 if banned == "1" else 0, uid),
        )
    return RedirectResponse("/admin/accounts", status_code=303)


@router.post("/accounts/{uid}/reset-token")
async def account_reset_token(request: Request, uid: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    with connect() as conn:
        conn.execute("UPDATE accounts SET token=? WHERE uid=?", (new_token("lab_"), uid))
    return RedirectResponse(f"/admin/accounts/{uid}", status_code=303)


@router.post("/accounts/{uid}/delete")
async def account_delete(request: Request, uid: int):
    redirect = need_login(request)
    if redirect:
        return redirect
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE account_id IN (SELECT id FROM accounts WHERE uid=?)", (uid,))
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
        {"admin": admin_name(request), "status": status, "gate_done": gate_done},
    )


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
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "admin": admin_name(request),
            "server_name": get_setting("server_name", SERVER_NAME),
            "announcement": get_setting("announcement", "欢迎来到本机明日方舟服务端"),
            "client_version": CLIENT_VERSION,
            "res_version": RES_VERSION,
        },
    )


@router.post("/settings")
async def settings_save(
    request: Request,
    server_name: str = Form(...),
    announcement: str = Form(""),
):
    redirect = need_login(request)
    if redirect:
        return redirect
    set_setting("server_name", server_name.strip() or SERVER_NAME)
    set_setting("announcement", announcement.strip())
    return RedirectResponse("/admin/settings", status_code=303)

