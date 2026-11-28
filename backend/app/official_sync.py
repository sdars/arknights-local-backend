"""Keep the advertised client/resource versions in step with the official API.

The synchroniser is intentionally small and dependency free.  It runs in a
background asyncio task, uses ETag revalidation when the configured URL
supports it, and never replaces the local version pair when a check fails or
returns an unexpected payload.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from . import runtime_settings
from .config import OFFICIAL_SYNC_INTERVAL, OFFICIAL_VERSION_URL

logger = logging.getLogger(__name__)

USER_AGENT = "ArknightsLocalBackend/1.0 (+official-version-sync)"
MAX_RESPONSE_BYTES = 1024 * 1024
MIN_INTERVAL = 60
MAX_INTERVAL = 24 * 60 * 60
RETRY_INTERVAL = 5 * 60
REQUEST_TIMEOUT = 20

_CLIENT_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")
_RES_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$")

STATUS_LABELS = {
    "never": "尚未检查",
    "updated": "已更新",
    "unchanged": "无变化",
    "not-modified": "已是最新（304）",
    "error": "同步失败",
    "disabled": "自动同步已关闭",
    "busy": "正在同步",
}

_sync_lock = threading.Lock()
_runner_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
_wake_event: asyncio.Event | None = None
_next_run_at: float | None = None
_last_attempt_at: float = 0.0


def _now_text() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_epoch(value: float | None) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _clamp_interval(value) -> int:
    try:
        interval = int(value)
    except (TypeError, ValueError):
        interval = OFFICIAL_SYNC_INTERVAL
    return max(MIN_INTERVAL, min(MAX_INTERVAL, interval))


def _effective_interval(settings: dict) -> int:
    interval = _clamp_interval(settings["official_sync_interval"])
    if settings["official_sync_last_status"] == "error":
        interval = min(interval, RETRY_INTERVAL)
    return interval


def _short(value, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def validate_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("官方版本接口必须是完整的 http(s) URL")


def _valid_client_version(value: str) -> bool:
    return bool(_CLIENT_VERSION_RE.match(value))


def _valid_res_version(value: str) -> bool:
    return bool(_RES_VERSION_RE.match(value))


def status() -> dict:
    """Return the persisted state plus in-process scheduler information."""
    settings = runtime_settings.values()
    interval = _clamp_interval(settings["official_sync_interval"])
    last_status = str(settings["official_sync_last_status"] or "never")
    return {
        "enabled": bool(settings["official_sync_enabled"]),
        "url": settings["official_sync_url"],
        "interval": interval,
        "interval_minutes": max(1, interval // 60),
        "retry_interval": RETRY_INTERVAL,
        "retry_interval_minutes": RETRY_INTERVAL // 60,
        "client": bool(settings["official_sync_client"]),
        "res": bool(settings["official_sync_res"]),
        "running": _sync_lock.locked(),
        "next_run": _format_epoch(_next_run_at),
        "last_attempt": settings["official_sync_last_attempt"],
        "last_success": settings["official_sync_last_success"],
        "last_status": last_status,
        "last_status_label": STATUS_LABELS.get(last_status, last_status),
        "last_error": settings["official_sync_last_error"],
        "official_client_version": settings["official_sync_last_client_version"],
        "official_res_version": settings["official_sync_last_res_version"],
        "last_duration_ms": settings["official_sync_last_duration_ms"],
    }


def _record_failure(url: str, started: float, message: str) -> dict:
    duration = int((time.time() - started) * 1000)
    error = _short(message)
    runtime_settings.save(
        {
            "official_sync_last_attempt": _now_text(),
            "official_sync_last_status": "error",
            "official_sync_last_error": error,
            "official_sync_last_duration_ms": duration,
        }
    )
    logger.warning("official version sync failed for %s: %s", url, error)
    return {
        "ok": False,
        "status": "error",
        "message": error,
        "duration_ms": duration,
    }


def _read_error_body(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read(4096)
    except Exception:
        return ""
    try:
        return body.decode("utf-8", errors="replace")
    except Exception:
        return repr(body)


def sync_once(trigger: str = "auto") -> dict:
    """Perform one synchronisation check.

    ``trigger="auto"`` honours the enabled flag.  The manual action bypasses
    that flag so an administrator can test the endpoint before enabling the
    recurring task.
    """
    if not _sync_lock.acquire(blocking=False):
        return {
            "ok": False,
            "status": "busy",
            "message": "已有官方版本同步任务正在执行",
        }
    try:
        return _sync_locked(trigger)
    finally:
        _sync_lock.release()


def _sync_locked(trigger: str) -> dict:
    settings = runtime_settings.values()
    if trigger == "auto" and not settings["official_sync_enabled"]:
        return {
            "ok": True,
            "status": "disabled",
            "message": "自动同步已关闭",
        }

    url = str(settings["official_sync_url"] or "").strip() or OFFICIAL_VERSION_URL
    started = time.time()
    try:
        validate_url(url)
    except ValueError as exc:
        return _record_failure(url, started, str(exc))

    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": USER_AGENT,
    }
    etag = str(settings["official_sync_etag"] or "").strip()
    if etag and settings["official_sync_etag_url"] == url:
        headers["If-None-Match"] = etag

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            response_etag = str(response.headers.get("ETag") or "").strip()
            response_status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            duration = int((time.time() - started) * 1000)
            now = _now_text()
            current = runtime_settings.values()
            updates = {
                "official_sync_last_attempt": now,
                "official_sync_last_success": now,
                "official_sync_last_error": "",
                "official_sync_last_duration_ms": duration,
                "official_sync_etag_url": url,
            }
            response_etag = str(exc.headers.get("ETag") or "").strip()
            if response_etag:
                updates["official_sync_etag"] = response_etag
            changed = []
            official_client = str(
                current["official_sync_last_client_version"] or ""
            ).strip()
            official_res = str(current["official_sync_last_res_version"] or "").strip()
            if (
                current["official_sync_client"]
                and _valid_client_version(official_client)
                and official_client != str(current["client_version"])
            ):
                updates["client_version"] = official_client
                changed.append("客户端版本")
            if (
                current["official_sync_res"]
                and _valid_res_version(official_res)
                and official_res != str(current["res_version"])
            ):
                updates["res_version"] = official_res
                changed.append("资源版本")
            if changed:
                updates["official_sync_last_status"] = "updated"
                message = f"官方接口返回 304，已回写{'、'.join(changed)}"
            else:
                updates["official_sync_last_status"] = "not-modified"
                message = "官方接口返回 304，当前版本已是最新"
            runtime_settings.save(updates)
            return {
                "ok": True,
                "status": updates["official_sync_last_status"],
                "message": message,
                "changed": changed,
                "duration_ms": duration,
            }
        detail = _short(_read_error_body(exc))
        message = f"官方接口返回 HTTP {exc.code}"
        if detail:
            message += f"：{detail}"
        return _record_failure(url, started, message)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return _record_failure(url, started, f"请求官方版本接口失败：{reason}")
    except Exception as exc:
        logger.exception("unexpected official version sync error")
        return _record_failure(url, started, f"同步异常：{exc}")

    if response_status != 200:
        return _record_failure(url, started, f"官方接口返回 HTTP {response_status}")
    if len(body) > MAX_RESPONSE_BYTES:
        return _record_failure(url, started, "官方接口响应超过 1 MiB，已拒绝")

    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _record_failure(url, started, f"官方接口返回的 JSON 无效：{exc}")
    if not isinstance(payload, dict):
        return _record_failure(url, started, "官方接口响应不是 JSON 对象")

    client_version = str(payload.get("clientVersion") or "").strip()
    res_version = str(payload.get("resVersion") or "").strip()
    if not client_version or not res_version:
        return _record_failure(url, started, "官方接口响应缺少 clientVersion/resVersion")
    if not _valid_client_version(client_version):
        return _record_failure(url, started, f"官方 clientVersion 格式非法：{client_version}")
    if not _valid_res_version(res_version):
        return _record_failure(url, started, f"官方 resVersion 格式非法：{res_version}")

    duration = int((time.time() - started) * 1000)
    now = _now_text()
    updates = {
        "official_sync_last_attempt": now,
        "official_sync_last_success": now,
        "official_sync_last_error": "",
        "official_sync_last_client_version": client_version,
        "official_sync_last_res_version": res_version,
        "official_sync_last_duration_ms": duration,
        "official_sync_etag_url": url,
        "official_sync_etag": response_etag,
    }
    changed = []
    if settings["official_sync_client"] and client_version != str(settings["client_version"]):
        updates["client_version"] = client_version
        changed.append("客户端版本")
    if settings["official_sync_res"] and res_version != str(settings["res_version"]):
        updates["res_version"] = res_version
        changed.append("资源版本")

    if changed:
        updates["official_sync_last_status"] = "updated"
        message = f"已同步{'、'.join(changed)}：{client_version} / {res_version}"
    else:
        updates["official_sync_last_status"] = "unchanged"
        message = f"官方版本无变化：{client_version} / {res_version}"
    runtime_settings.save(updates)
    logger.info("official version sync %s: %s", updates["official_sync_last_status"], message)
    return {
        "ok": True,
        "status": updates["official_sync_last_status"],
        "message": message,
        "client_version": client_version,
        "res_version": res_version,
        "changed": changed,
        "duration_ms": duration,
    }


async def _wait(timeout: float) -> None:
    event = _wake_event
    if event is None:
        await asyncio.sleep(max(0.1, timeout))
        return
    try:
        await asyncio.wait_for(event.wait(), timeout=max(0.1, timeout))
    except asyncio.TimeoutError:
        pass
    else:
        event.clear()


async def _runner_loop() -> None:
    global _next_run_at, _last_attempt_at
    # Give the HTTP server and database a moment to finish starting up.
    await asyncio.sleep(2)
    while _stop_event is not None and not _stop_event.is_set():
        try:
            settings = runtime_settings.values()
            interval = _effective_interval(settings)
            if not settings["official_sync_enabled"]:
                _next_run_at = None
                await _wait(60)
                continue
            now = time.time()
            due = (_last_attempt_at + interval) if _last_attempt_at else now
            if now >= due:
                try:
                    await asyncio.to_thread(sync_once, "auto")
                except Exception:
                    logger.exception("background official version sync crashed")
                _last_attempt_at = time.time()
                _next_run_at = _last_attempt_at + _effective_interval(
                    runtime_settings.values()
                )
                continue
            _next_run_at = due
            await _wait(min(60.0, max(1.0, due - now)))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("official version sync loop error")
            await _wait(60)


def start_background() -> None:
    global _runner_task, _stop_event, _wake_event
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _runner_task is not None and not _runner_task.done():
        return
    _stop_event = asyncio.Event()
    _wake_event = asyncio.Event()
    _runner_task = loop.create_task(_runner_loop(), name="official-version-sync")
    logger.info("official version background synchroniser started")


async def stop_background() -> None:
    global _runner_task, _stop_event, _wake_event
    task = _runner_task
    if task is None:
        return
    if _stop_event is not None:
        _stop_event.set()
    if _wake_event is not None:
        _wake_event.set()
    try:
        await asyncio.wait_for(task, timeout=8)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    finally:
        _runner_task = None
        _stop_event = None
        _wake_event = None


def wake() -> None:
    """Make the scheduler recalculate its next run after settings change."""
    event = _wake_event
    if event is not None:
        event.set()


async def sync_now(trigger: str = "manual") -> dict:
    global _last_attempt_at, _next_run_at
    result = await asyncio.to_thread(sync_once, trigger)
    if result.get("status") != "busy":
        _last_attempt_at = time.time()
        settings = runtime_settings.values()
        if settings["official_sync_enabled"]:
            _next_run_at = _last_attempt_at + _effective_interval(settings)
        else:
            _next_run_at = None
    wake()
    return result

