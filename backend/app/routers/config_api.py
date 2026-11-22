import json
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import JSONResponse, RedirectResponse

from ..config import ASSET_CDN, CONFIG_VER, FUNC_VER, RES_VERSION, CLIENT_VERSION, SERVER_URL, APP_CODE

router = APIRouter()


def network_config():
    return {
        "an": f"{SERVER_URL}/config/prod/announce_meta/{{0}}/announcement.meta.json",
        "as": SERVER_URL,
        "gs": SERVER_URL,
        "hu": ASSET_CDN,
        "hv": f"{SERVER_URL}/config/prod/official/{{0}}/version",
        "of": "https://ak.hypergryph.com/index.html",
        "sl": "https://ak.hypergryph.com/protocol/service",
        "u8": f"{SERVER_URL}/u8",
        "pkgAd": "https://ak.hypergryph.com/download",
        "prean": "https://ak-webview.hypergryph.com",
        "devsdk": False,
        "pkgIOS": "https://apps.apple.com/cn/app/id1454663939",
        "configVer": CONFIG_VER,
        "funcVer": FUNC_VER,
        "secure": False,
    }


def signed_network_config():
    content = {
        "configVer": CONFIG_VER,
        "funcVer": FUNC_VER,
        "configs": {
            FUNC_VER: {
                "override": True,
                "network": {
                    "gs": SERVER_URL,
                    "as": SERVER_URL,
                    "u8": f"{SERVER_URL}/u8",
                    "hu": ASSET_CDN,
                    "hv": f"{SERVER_URL}/config/prod/official/{{0}}/version",
                    "rc": f"{SERVER_URL}/config/prod/official/remote_config",
                    "an": f"{SERVER_URL}/config/prod/announce_meta/{{0}}/announcement.meta.json",
                    "prean": "https://ak-webview.hypergryph.com",
                    "sl": "https://ak.hypergryph.com/protocol/service",
                    "of": "https://ak.hypergryph.com/index.html",
                    "pkgAd": "https://ak.hypergryph.com/download",
                    "pkgIOS": "https://apps.apple.com/cn/app/id1454663939",
                    "secure": False,
                    "devsdk": False,
                },
            }
        },
    }
    return {"sign": "local-selfhost-sign", "content": json.dumps(content, separators=(",", ":"))}


@router.get("/")
async def root():
    return signed_network_config()


@router.get("/api/remote_config/1/prod/default/{os_name}/network_config")
async def api_network_config(os_name: str):
    return network_config()


@router.get("/api/remote_config/1/prod/default/{os_name}/remote_config")
async def api_remote_config(os_name: str):
    return {"fapv2": 1, "HGDownload_1": 10000, "HGDownload_2": 10000, "enableGameBI": False, "enableNativeLicense": False}


@router.get("/api/remote_config/104/prod-download/default/{os_name}/download-remote-config")
async def download_remote_config(os_name: str):
    return {"use-assets": False}


@router.get("/api/remote_config/101/prod/default/{os_name}/ak_sdk_config")
async def ak_sdk_config(os_name: str):
    return {"report_device_info": 10000}


@router.get("/api/game/get_latest_game_info")
async def latest_game_info():
    return {
        "action": 0,
        "version": "79.0.0",
        "update_type": 0,
        "update_info": {
            "patch": None,
            "custom_info": "",
            "source_package": None,
            "package": {
                "packs": [],
                "total_size": "0",
                "file_path": "",
                "url": "",
                "md5": "",
                "package_size": "0",
                "file_id": "1",
                "sub_channel": "1",
                "game_files_md5": "",
            },
        },
        "client_version": CLIENT_VERSION,
        "state": 0,
        "preload_action": 0,
        "preload_info": {
            "res_version": "",
            "res_game_version": "",
            "package_size": "0",
            "total_size": "0",
            "patches": [],
            "cd_key": "",
            "source_res_version": "",
        },
    }


@router.get("/config/prod/official/Android/version")
@router.get("/config/prod/official/Windows/version")
@router.get("/config/prod/official/ios/version")
async def version():
    return {"resVersion": RES_VERSION, "clientVersion": CLIENT_VERSION}


@router.get("/config/prod/official/network_config")
async def signed_config():
    return signed_network_config()


@router.get("/config/prod/official/remote_config")
async def official_remote_config():
    return {"fapv2": 1, "HGDownload_1": 10000, "HGDownload_2": 10000, "enableGameBI": False}


@router.get("/config/prod/announce_meta/{os_name}/announcement.meta.json")
async def announce_meta(os_name: str):
    return {
        "preAnnounceId": "528",
        "actived": True,
        "preAnnounceType": 2,
        "announcement": {"focusAnnounceId": "", "announceList": []},
        "status": {"announcementVersion": "1", "announcementPopUpVersion": "1"},
    }


@router.get("/assetbundle/official/{os_name}/assets/{assets_hash}/{file_name:path}")
async def asset_bundle(os_name: str, assets_hash: str, file_name: str):
    if not assets_hash.replace("-", "").replace("_", "").isalnum() or ".." in file_name:
        return JSONResponse({"error": "bad request"}, status_code=400)
    target = f"{ASSET_CDN}/{os_name}/assets/{assets_hash}/{quote(file_name)}"
    return RedirectResponse(target, status_code=302)


@router.get("/api/gate/meta/Android")
async def gate_meta():
    return {"preAnnounceId": "528", "actived": True, "preAnnounceType": 2}

