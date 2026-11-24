import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import APP_DIR, SECRET_KEY
from .db import init_db, log_request
from . import battles, mail_service, payments
from .routers import admin, config_api, game, sdk

app = FastAPI(title="Arknights Local Backend", docs_url=None, redoc_url=None)
logger = logging.getLogger(__name__)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.middleware("http")
async def request_logger(request: Request, call_next):
    started = time.time()
    try:
        response = await call_next(request)
    except Exception:
        log_request(
            request.method,
            request.url.path,
            500,
            request.headers.get("uid"),
            request.client.host if request.client else None,
            int((time.time() - started) * 1000),
        )
        raise
    if not request.url.path.startswith("/static") and not request.url.path.startswith("/admin"):
        log_request(
            request.method,
            request.url.path,
            response.status_code,
            request.headers.get("uid"),
            request.client.host if request.client else None,
            int((time.time() - started) * 1000),
        )
    ctype = response.headers.get("content-type", "")
    if ctype.startswith("application/json") and "charset" not in ctype.lower():
        response.headers["content-type"] = "application/json; charset=utf-8"
    return response


@app.on_event("startup")
async def on_startup():
    init_db()
    try:
        payments.backfill_paid_order_grants()
    except Exception:
        pass
    try:
        payments.reconcile_gift_purchase_state()
    except Exception:
        pass
    try:
        mail_service.seed_templates()
    except Exception:
        pass
    try:
        battles.repair_account_tutorial_states()
    except Exception:
        pass


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "arknights-local-backend"}


app.include_router(admin.router)
app.include_router(sdk.router)
app.include_router(config_api.router)
app.include_router(game.router)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def fallback(path: str, request: Request):
    if path.startswith("admin") or path.startswith("static"):
        return JSONResponse({"error": "not found"}, status_code=404)
    logger.warning("unmatched route: %s /%s uid=%s", request.method, path, request.headers.get("uid"))
    return {
        "result": 0,
        "playerDataDelta": {"deleted": {}, "modified": {}},
    }

