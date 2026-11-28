import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import APP_DIR, SECRET_KEY
from .db import init_db, log_request
from . import battles, mail_service, official_sync, payments, runtime_settings
from .routers import (
    admin,
    config_api,
    features_activity,
    features_autochess,
    features_building,
    features_campaign,
    features_deep_sea,
    features_depot,
    features_misc,
    features_rlv2,
    features_sandbox,
    features_tower,
    features_world,
    game,
    legacy,
    sdk,
)

app = FastAPI(title="Arknights Local Backend", docs_url=None, redoc_url=None)
logger = logging.getLogger(__name__)
PROCESS_STARTED_AT = time.time()
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


def _safe_log_request(*args):
    """Keep request logging from turning an otherwise valid response into 500."""
    try:
        log_request(*args)
    except Exception:
        logger.exception("request log write failed")


@app.middleware("http")
async def request_logger(request: Request, call_next):
    started = time.time()
    try:
        response = await call_next(request)
    except Exception:
        _safe_log_request(
            request.method,
            request.url.path,
            500,
            request.headers.get("uid"),
            request.client.host if request.client else None,
            int((time.time() - started) * 1000),
        )
        raise
    if not request.url.path.startswith("/static") and not request.url.path.startswith("/admin"):
        _safe_log_request(
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
    official_sync.start_background()


@app.on_event("shutdown")
async def on_shutdown():
    await official_sync.stop_background()


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "arknights-local-backend"}


@app.get("/version")
async def version():
    settings = runtime_settings.values()
    return {
        "version": settings["client_version"],
        "resVersion": settings["res_version"],
        "service": "arknights-local-backend",
    }


@app.get("/metrics")
async def metrics():
    """Small Prometheus-compatible scrape target used by local probes."""
    settings = runtime_settings.values()
    uptime = max(0, int(time.time() - PROCESS_STARTED_AT))
    client_version = str(settings["client_version"]).replace("\\", "\\\\").replace('"', '\\"')
    res_version = str(settings["res_version"]).replace("\\", "\\\\").replace('"', '\\"')
    body = "\n".join(
        [
            "# HELP ark_backend_up Backend process is running",
            "# TYPE ark_backend_up gauge",
            "ark_backend_up 1",
            "# HELP ark_backend_uptime_seconds Backend process uptime in seconds",
            "# TYPE ark_backend_uptime_seconds gauge",
            f"ark_backend_uptime_seconds {uptime}",
            "# HELP ark_backend_client_version_info Advertised client version",
            "# TYPE ark_backend_client_version_info gauge",
            f'ark_backend_client_version_info{{version="{client_version}"}} 1',
            "# HELP ark_backend_res_version_info Advertised resource version",
            "# TYPE ark_backend_res_version_info gauge",
            f'ark_backend_res_version_info{{version="{res_version}"}} 1',
            "",
        ]
    )
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")


@app.get("/favicon.ico")
async def favicon():
    return Response(
        content=(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<rect width="64" height="64" rx="12" fill="#111827"/>'
            '<path d="M16 46 32 14l16 32-16-8z" fill="#d2b56d"/>'
            '</svg>'
        ),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.head("/")
async def root_head():
    return Response(status_code=200)


@app.options("/")
async def root_options():
    return Response(status_code=204, headers={"Allow": "GET, HEAD, OPTIONS"})


app.include_router(admin.router)
app.include_router(sdk.router)
app.include_router(config_api.router)
app.include_router(game.router)
app.include_router(legacy.router)
app.include_router(features_misc.router)
app.include_router(features_building.router)
app.include_router(features_depot.router)
app.include_router(features_world.router)
app.include_router(features_campaign.router)
app.include_router(features_tower.router)
app.include_router(features_deep_sea.router)
app.include_router(features_autochess.router)
app.include_router(features_sandbox.router)
app.include_router(features_rlv2.router)
app.include_router(features_activity.router)


@app.exception_handler(404)
async def not_found(request: Request, _exc):
    """Never mask an unimplemented game endpoint with a fake success."""
    logger.warning("unmatched route: %s %s uid=%s", request.method, request.url.path, request.headers.get("uid"))
    return JSONResponse(
        {
            "result": 1,
            "error": "route not implemented",
            "path": request.url.path,
        },
        status_code=404,
    )

