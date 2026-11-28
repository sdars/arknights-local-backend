from .config import (
    ASSET_CDN,
    CLIENT_VERSION,
    OFFICIAL_SYNC_INTERVAL,
    OFFICIAL_VERSION_URL,
    RES_VERSION,
    SERVER_NAME,
)
from .db import get_setting, set_setting


def _bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def values():
    return {
        "server_name": get_setting("server_name", SERVER_NAME),
        "announcement": get_setting("announcement", "欢迎来到本机明日方舟服务端"),
        "client_version": get_setting("client_version", CLIENT_VERSION),
        "res_version": get_setting("res_version", RES_VERSION),
        "force_update": _bool(get_setting("force_update"), True),
        "force_launcher_update": _bool(get_setting("force_launcher_update"), False),
        "force_resource_update": _bool(get_setting("force_resource_update"), False),
        "maintenance": _bool(get_setting("maintenance"), False),
        "maintenance_message": get_setting("maintenance_message", "服务器维护中，请稍后再试"),
        "asset_base_url": get_setting("asset_base_url", ASSET_CDN),
        "asset_fallback_url": get_setting("asset_fallback_url", ASSET_CDN),
        "asset_root": get_setting("asset_root", ""),
        "update_url": get_setting("update_url", ""),
        "update_notes": get_setting("update_notes", ""),
        "official_sync_enabled": _bool(get_setting("official_sync_enabled"), True),
        "official_sync_url": get_setting("official_sync_url", OFFICIAL_VERSION_URL),
        "official_sync_interval": _int(
            get_setting("official_sync_interval"), OFFICIAL_SYNC_INTERVAL
        ),
        "official_sync_client": _bool(get_setting("official_sync_client"), True),
        "official_sync_res": _bool(get_setting("official_sync_res"), True),
        "official_sync_last_attempt": get_setting("official_sync_last_attempt", ""),
        "official_sync_last_success": get_setting("official_sync_last_success", ""),
        "official_sync_last_status": get_setting("official_sync_last_status", "never"),
        "official_sync_last_error": get_setting("official_sync_last_error", ""),
        "official_sync_last_client_version": get_setting(
            "official_sync_last_client_version", ""
        ),
        "official_sync_last_res_version": get_setting(
            "official_sync_last_res_version", ""
        ),
        "official_sync_last_duration_ms": _int(
            get_setting("official_sync_last_duration_ms"), 0
        ),
        "official_sync_etag": get_setting("official_sync_etag", ""),
        "official_sync_etag_url": get_setting("official_sync_etag_url", ""),
    }


def save(values_map):
    for key in (
        "server_name",
        "announcement",
        "client_version",
        "res_version",
        "force_update",
        "force_launcher_update",
        "force_resource_update",
        "maintenance",
        "maintenance_message",
        "asset_base_url",
        "asset_fallback_url",
        "asset_root",
        "update_url",
        "update_notes",
        "official_sync_enabled",
        "official_sync_url",
        "official_sync_interval",
        "official_sync_client",
        "official_sync_res",
        "official_sync_last_attempt",
        "official_sync_last_success",
        "official_sync_last_status",
        "official_sync_last_error",
        "official_sync_last_client_version",
        "official_sync_last_res_version",
        "official_sync_last_duration_ms",
        "official_sync_etag",
        "official_sync_etag_url",
    ):
        if key in values_map:
            value = values_map[key]
            if isinstance(value, bool):
                value = "1" if value else "0"
            set_setting(key, value)
