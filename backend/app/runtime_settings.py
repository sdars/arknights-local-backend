from .config import ASSET_CDN, CLIENT_VERSION, RES_VERSION, SERVER_NAME
from .db import get_setting, set_setting


def _bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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
    ):
        if key in values_map:
            value = values_map[key]
            if isinstance(value, bool):
                value = "1" if value else "0"
            set_setting(key, value)
