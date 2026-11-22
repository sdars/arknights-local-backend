import fcntl
import json
import os
import re
import subprocess
import ssl
import struct
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
JUNIT_PATH = LOGS / "protocol-junit.xml"
TEST_LOG_PATH = LOGS / "protocol-tests.log"
WATCHDOG_LOG_PATH = LOGS / "watchdog.log"
TEST_LOCK_PATH = LOGS / ".protocol-tests.lock"

ARTIFACT_PATTERNS = {
    "libil2cpp": "tmp/逆向分析/**/libil2cpp.so",
    "raw_metadata": "tmp/逆向分析/**/global-metadata.dat",
    "decrypted_metadata": "tmp/逆向分析/**/global-metadata.decrypted.dat",
    "dump_cs": "tmp/逆向分析/**/dump.cs",
    "stringliteral": "tmp/逆向分析/**/stringliteral.json",
    "script_json": "tmp/逆向分析/**/script.json",
    "il2cpp_header": "tmp/逆向分析/**/il2cpp.h",
    "protocol_catalog": "backend/data/protocol_catalog.json",
}

ARTIFACT_LABELS = {
    "libil2cpp": "libil2cpp.so",
    "raw_metadata": "global-metadata.dat（原始）",
    "decrypted_metadata": "global-metadata.decrypted.dat",
    "dump_cs": "dump.cs",
    "stringliteral": "stringliteral.json",
    "script_json": "script.json",
    "il2cpp_header": "il2cpp.h",
    "protocol_catalog": "protocol_catalog.json",
}

_CACHE = {"ts": 0.0, "data": None}
_ADB_PROBE_CACHE = {"ts": 0.0, "data": None}
_SDK_GATEWAY_CACHE = {"ts": 0.0, "data": None}

PROTOCOL_ENDPOINTS = [
    {"stage": "SDK", "method": "GET", "path": "/app/v1/config", "test": "test_sdk_config", "desc": "应用配置"},
    {"stage": "SDK", "method": "GET", "path": "/general/v1/server_time", "test": "test_server_time", "desc": "服务器时间"},
    {"stage": "SDK", "method": "GET", "path": "/api/remote_config/1/prod/default/Android/network_config", "test": "test_network_config", "desc": "网络配置"},
    {"stage": "SDK", "method": "GET", "path": "/user/info/v1/basic", "test": "test_basic", "desc": "账号基础信息"},
    {"stage": "SDK", "method": "POST", "path": "/user/oauth2/v2/grant", "test": "test_grant", "desc": "OAuth grant"},
    {"stage": "SDK", "method": "GET", "path": "bridge:/user/info/v1/basic", "test": "test_bridge_sdk_envelope_and_stale_token_self_heal", "desc": "反向层 HGSDK 信封与过期 token 自愈"},
    {"stage": "SDK", "method": "POST", "path": "/u8/user/v1/getToken", "test": "test_u8_get_token", "desc": "U8 渠道 token"},
    {"stage": "SDK", "method": "POST", "path": "/u8/pay/getAllProductList", "test": "test_product_list", "desc": "商品列表"},
    {"stage": "登录", "method": "POST", "path": "/account/login", "test": "test_account_login", "desc": "游戏登录"},
    {"stage": "登录", "method": "POST", "path": "/account/syncData", "test": "test_sync_data", "desc": "玩家数据同步"},
    {"stage": "登录", "method": "POST", "path": "/account/syncStatus", "test": "test_sync_status", "desc": "在线状态同步"},
    {"stage": "商店", "method": "POST", "path": "/shop/getCashGoodList", "test": "test_cash_good_list_has_goods", "desc": "商店列表"},
    {"stage": "商店", "method": "POST", "path": "/shop/getGPGoodList", "test": "test_shop_lists_are_populated", "desc": "组合包列表"},
    {"stage": "支付", "method": "POST", "path": "/pay/createOrder", "test": "test_create_order_returns_sdk_extension", "desc": "游戏订单"},
    {"stage": "支付", "method": "POST", "path": "/pay/createOrderAlipay", "test": "test_hgsdk_alipay_page_pay_and_success_callback", "desc": "支付宝页支付"},
    {"stage": "支付", "method": "POST", "path": "/pay/confirmOrderAlipay", "test": "test_hgsdk_alipay_confirm_marks_paid", "desc": "支付宝回调"},
    {"stage": "支付", "method": "POST", "path": "/pay/createOrderWechat", "test": "test_hgsdk_wechat_bill_shape", "desc": "微信支付单"},
    {"stage": "支付", "method": "POST", "path": "/pay/order/v2/check/alipay", "test": "test_check_order_v2_returns_sdk_status", "desc": "支付渠道校验"},
    {"stage": "支付", "method": "POST", "path": "/u8/pay/confirmOrderState", "test": "test_u8_confirm_order_state_reports_succeed", "desc": "订单状态确认"},
    {"stage": "支付", "method": "POST", "path": "/pay/confirmOrder", "test": "test_confirm_order_applies_and_persists_rewards", "desc": "确认领取、落库与幂等"},
    {"stage": "支付", "method": "POST", "path": "/pay/confirmOrder#cash", "test": "test_cash_order_grants_first_purchase_and_repeat_originium", "desc": "源石首购双倍、复购与幂等"},
    {"stage": "支付", "method": "POST", "path": "/pay/confirmOrder#cash-legacy", "test": "test_legacy_cash_product_id_uses_current_tier_reward", "desc": "旧源石商品 ID 映射"},
    {"stage": "支付", "method": "POST", "path": "/pay/confirmOrder#monthly-card", "test": "test_monthly_card_purchase_activates_subscription_and_daily_bonus", "desc": "月卡订阅、每日奖励与幂等"},
    {"stage": "任务", "method": "POST", "path": "/mission/confirmMission", "test": "test_confirm_mission_grants_and_persists", "desc": "单任务领奖与持久化"},
    {"stage": "任务", "method": "POST", "path": "/mission/confirmMissionList", "test": "test_confirm_mission_list_and_group_rewards", "desc": "批量任务领奖"},
    {"stage": "任务", "method": "POST", "path": "/mission/autoConfirmMissions", "test": "test_confirm_mission_list_and_group_rewards", "desc": "按类型自动领奖"},
    {"stage": "任务", "method": "POST", "path": "/mission/confirmMissionGroup", "test": "test_confirm_mission_list_and_group_rewards", "desc": "任务组奖励"},
    {"stage": "任务", "method": "POST", "path": "/mission/exchangeMissionRewards", "test": "test_exchange_mission_point_reward", "desc": "任务点数兑换"},
    {"stage": "签到", "method": "POST", "path": "/user/checkIn", "test": "test_checkin_grants_once_per_day", "desc": "每日签到与幂等"},
    {"stage": "签到", "method": "POST", "path": "/openServerV3/getCheckInReward", "test": "test_open_server_checkin_grants_once_and_persists", "desc": "累计签到发奖、落库与幂等"},
    {"stage": "签到", "method": "POST", "path": "/activity/getOpenServerCheckInReward", "test": "test_legacy_open_server_checkin_route_and_day_two_inventory", "desc": "累计签到兼容路由"},
    {"stage": "邮件", "method": "POST", "path": "/mail/getMetaInfoList", "test": "test_mail_protocol_grants_once_and_persists", "desc": "邮件元数据与 createTime 契约"},
    {"stage": "邮件", "method": "POST", "path": "/mail/listMailBox", "test": "test_mail_protocol_grants_once_and_persists", "desc": "邮件正文、附件与类型"},
    {"stage": "邮件", "method": "POST", "path": "/mail/receiveMail", "test": "test_mail_protocol_grants_once_and_persists", "desc": "单封领取、防重与落库"},
    {"stage": "邮件", "method": "POST", "path": "/mail/receiveAllMail", "test": "test_mail_receive_all_remove_and_meta_disappears", "desc": "批量领取与删除"},
    {"stage": "编队", "method": "POST", "path": "/quest/squadFormation", "test": "test_account_console_operators_squads_and_protocol", "desc": "协议编队与槽位持久化"},
    {"stage": "编队", "method": "POST", "path": "/quest/changeSquadName", "test": "test_account_console_operators_squads_and_protocol", "desc": "编队改名"},
    {"stage": "战斗", "method": "POST", "path": "/quest/battleStart", "test": "test_battle_start", "desc": "开始战斗"},
    {"stage": "战斗", "method": "POST", "path": "/quest/battleFinish", "test": "test_battle_finish", "desc": "结算、首通与关卡落库"},
    {"stage": "后台", "method": "GET", "path": "/admin/login", "test": "test_admin_login_page", "desc": "管理后台登录页"},
    {"stage": "后台", "method": "GET", "path": "/admin/accounts/{uid}", "test": "test_account_console_status_inventory_and_assets", "desc": "用户控制台：货币/库存/资产"},
    {"stage": "后台", "method": "POST", "path": "/admin/accounts/{uid}/operator-add", "test": "test_account_console_operators_squads_and_protocol", "desc": "干员新增、属性与编队"},
    {"stage": "后台", "method": "GET", "path": "/admin/mail", "test": "test_admin_mail_page_and_template", "desc": "邮件模板中心"},
    {"stage": "更新", "method": "GET", "path": "/config/prod/official/Android/version", "test": "test_force_update_maintenance_and_resource_delivery", "desc": "动态客户端/资源版本"},
    {"stage": "更新", "method": "GET", "path": "/api/game/get_latest_game_info", "test": "test_force_update_maintenance_and_resource_delivery", "desc": "强制更新与 APK 地址"},
    {"stage": "资源", "method": "GET", "path": "/assetbundle/official/...", "test": "test_force_update_maintenance_and_resource_delivery", "desc": "本地资源、HEAD 与回源"},
    {"stage": "研发", "method": "GET", "path": "/admin/protocol", "test": "test_protocol_index_and_api", "desc": "静态协议索引"},
    {"stage": "持久化", "method": "POST", "path": "/account/syncData", "test": "test_sync_data_persists_last_refresh_ts", "desc": "lastRefreshTs 持久化"},
]


def _env_value(key, default=""):
    value = os.environ.get(key)
    if value:
        return value
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, val = line.split("=", 1)
            if name.strip() == key:
                return val.strip().strip('"').strip("'")
    return default


def _run(cmd, timeout=5):
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (proc.stdout + proc.stderr).strip()
        return proc.returncode, output
    except Exception as exc:
        return 124, str(exc)


def _service_rows():
    rows = []
    for unit, label in (
        ("ark-backend.service", "Python 后端"),
        ("ark-mitm.service", "TLS 反向层"),
        ("ark-watchdog.service", "设备 watchdog"),
    ):
        _, out = _run(["systemctl", "is-active", unit], timeout=3)
        active = out.strip() == "active"
        rows.append({"unit": unit, "label": label, "active": active, "state": out.strip() or "unknown"})
    return rows


def _backend_status(services):
    backend = next((row for row in services if row["unit"] == "ark-backend.service"), None)
    try:
        with urlopen("http://127.0.0.1:8443/healthz", timeout=2) as response:
            body = json.loads(response.read().decode())
        return {"ok": bool(body.get("ok")), "detail": "healthz 200", "active": bool(backend and backend["active"])}
    except Exception as exc:
        return {"ok": False, "detail": str(exc), "active": bool(backend and backend["active"])}


def _adb_status(detailed=False):
    serial = _env_value("ADB_SERIAL", "")
    result = {
        "serial": serial,
        "state": "未配置",
        "reverse": [],
        "proxy": "",
        "https_forward": False,
        "app_uid": "",
        "hosts_mapped": 0,
        "hosts_ok": False,
        "local_route_count": 0,
        "local_route_ok": False,
        "device_backend_ok": False,
        "app_tls_ok": False,
        "app_tls_detail": "",
    }
    if not serial:
        return result
    _, state = _run(["adb", "-s", serial, "get-state"], timeout=5)
    result["state"] = state.strip() or "offline"
    _, reverse = _run(["adb", "-s", serial, "reverse", "--list"], timeout=5)
    result["reverse"] = [line.strip() for line in reverse.splitlines() if line.strip()]
    _, proxy = _run(["adb", "-s", serial, "shell", "settings", "get", "global", "http_proxy"], timeout=5)
    result["proxy"] = proxy.strip()
    rc, _ = _run(
        ["adb", "-s", serial, "shell", 'ps -A -o ARGS | grep "[n]c -p 443 -L nc 127.0.0.1 8444"'],
        timeout=5,
    )
    result["https_forward"] = rc == 0
    _, uid_out = _run(
        ["adb", "-s", serial, "shell", "dumpsys package com.hypergryph.arknights | grep -m1 'userId='"],
        timeout=5,
    )
    uid_match = re.search(r"userId=(\d+)", uid_out)
    if uid_match:
        result["app_uid"] = uid_match.group(1)
    _, hosts_out = _run(
        [
            "adb",
            "-s",
            serial,
            "shell",
            "grep -cE '127\\.0\\.0\\.1 (as|game-config|ak-conf|launcher|ak-webview)\\.hypergryph\\.com|127\\.0\\.0\\.1 openapi\\.alipay\\.com' /system/etc/hosts",
        ],
        timeout=5,
    )
    result["hosts_mapped"] = int(hosts_out.strip()) if hosts_out.strip().isdigit() else 0
    result["hosts_ok"] = result["hosts_mapped"] >= 6
    _, rules_out = _run(
        ["adb", "-s", serial, "shell", "su -M -c 'iptables -t nat -S ARK_LOCAL 2>/dev/null'"],
        timeout=8,
    )
    route_lines = [line for line in rules_out.splitlines() if "-A ARK_LOCAL" in line and "DNAT" in line]
    result["local_route_count"] = len(route_lines)
    result["local_route_ok"] = bool(route_lines)
    if detailed and (not _ADB_PROBE_CACHE["data"] or time.time() - _ADB_PROBE_CACHE["ts"] >= 30):
        probe = {}
        rc, http_out = _run(
            ["adb", "-s", serial, "shell", "/system/bin/curl -sS -m 4 http://127.0.0.1:8443/healthz"],
            timeout=7,
        )
        probe["device_backend_ok"] = rc == 0 and '"ok":true' in http_out
        if result["app_uid"]:
            app_cmd = "su -M -c \"su %s -c '/system/bin/curl -k -sS -m 6 https://launcher.hypergryph.com/general/v1/server_time'\"" % result["app_uid"]
            rc, tls_out = _run(["adb", "-s", serial, "shell", app_cmd], timeout=20)
            probe["app_tls_ok"] = rc == 0 and '"status":0' in tls_out and "aliyungf" not in tls_out
            probe["app_tls_detail"] = tls_out.strip()[:240]
        _ADB_PROBE_CACHE.update(ts=time.time(), data=probe)
    if detailed and _ADB_PROBE_CACHE["data"]:
        result.update(_ADB_PROBE_CACHE["data"])
    return result


def adb_diagnostics():
    result = _adb_status(detailed=True)
    result["sdk_gateway"] = _sdk_gateway_status()
    return result


def _sdk_gateway_status():
    now = time.time()
    if _SDK_GATEWAY_CACHE["data"] is not None and now - _SDK_GATEWAY_CACHE["ts"] < 10:
        return _SDK_GATEWAY_CACHE["data"]
    expected_uid = str(_env_value("ARK_UID", "10000000"))
    configured_token = _env_value("ARK_TOKEN", "lab_manual_token")
    context = ssl._create_unverified_context()

    def probe(token):
        query = urlencode({"token": token})
        url = f"https://127.0.0.1:4443/user/info/v1/basic?{query}"
        with urlopen(url, context=context, timeout=4) as response:
            envelope = json.loads(response.read().decode("utf-8"))
        if int(envelope.get("status", -1)) != 0:
            return False, f"envelope status={envelope.get('status')}"
        data = envelope.get("data")
        if not isinstance(data, str):
            return False, "data 不是字符串"
        data = json.loads(data)
        if not isinstance(data, dict):
            return False, "data 不是对象"
        if str(data.get("identityNum")) != expected_uid:
            return False, f"identityNum={data.get('identityNum')} 期望 {expected_uid}"
        return True, "status=0 / data 字符串 / identityNum 正确"

    result = {"ok": False, "envelope_ok": False, "token_heal_ok": False, "detail": ""}
    try:
        result["envelope_ok"], envelope_detail = probe(configured_token)
        result["token_heal_ok"], heal_detail = probe("__ark_stale_token__")
        result["ok"] = result["envelope_ok"] and result["token_heal_ok"]
        result["detail"] = f"正常 token: {envelope_detail}；过期 token: {heal_detail}"
    except Exception as exc:
        result["detail"] = str(exc)
    _SDK_GATEWAY_CACHE.update(ts=now, data=result)
    return result


def repair_adb():
    _ADB_PROBE_CACHE.update(ts=0.0, data=None)
    _SDK_GATEWAY_CACHE.update(ts=0.0, data=None)
    rc, output = _run([str(ROOT / "scripts" / "watchdog.sh")], timeout=60)
    return {"ok": rc == 0, "rc": rc, "output": output[-4000:]}


def _newest_artifact(pattern):
    matches = [
        path
        for path in ROOT.glob(pattern)
        if path.is_file() and "il2cppdumper" not in path.parts and "tools" not in path.parts
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def _artifact_row(key, pattern):
    path = _newest_artifact(pattern)
    if not path:
        return {
            "key": key,
            "label": ARTIFACT_LABELS.get(key, key),
            "path": "",
            "exists": False,
            "valid": False,
            "size": 0,
            "mtime": "",
            "detail": "未找到",
        }
    stat = path.stat()
    valid = True
    detail = ""
    if key == "libil2cpp":
        valid, detail = _elf_complete(path)
    return {
        "key": key,
        "label": ARTIFACT_LABELS.get(key, key),
        "path": str(path.relative_to(ROOT)),
        "exists": True,
        "valid": valid,
        "size": stat.st_size,
        "size_human": _format_size(stat.st_size),
        "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "detail": detail,
    }


def artifact_path(key):
    pattern = ARTIFACT_PATTERNS.get(key)
    return _newest_artifact(pattern) if pattern else None


def _elf_complete(path):
    try:
        with open(path, "rb") as stream:
            header = stream.read(64)
        if len(header) < 64 or header[:4] != b"\x7fELF" or header[4] != 2:
            return False, "不是 ELF64"
        shoff = struct.unpack_from("<Q", header, 0x28)[0]
        shentsize = struct.unpack_from("<H", header, 0x3A)[0]
        shnum = struct.unpack_from("<H", header, 0x3C)[0]
        required = shoff + shentsize * shnum
        size = path.stat().st_size
        if required > size:
            return False, f"ELF 不完整：需要 {required:,} 字节，当前 {size:,}"
        return True, "ELF section header 完整"
    except Exception as exc:
        return False, f"ELF 校验失败：{exc}"


def _format_size(size):
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _metadata_status():
    raw = _newest_artifact(ARTIFACT_PATTERNS["raw_metadata"])
    decrypted = _newest_artifact(ARTIFACT_PATTERNS["decrypted_metadata"])
    dump_cs = _newest_artifact(ARTIFACT_PATTERNS["dump_cs"])
    magic = b"\xaf\x1b\xb1\xfa"
    result = {
        "state": "未找到",
        "detail": "",
        "raw": str(raw.relative_to(ROOT)) if raw else "",
        "decrypted": str(decrypted.relative_to(ROOT)) if decrypted else "",
        "dump_cs": bool(dump_cs),
    }
    if decrypted and decrypted.exists():
        header = decrypted.read_bytes()[:4]
        if header == magic:
            result.update(state="已解密", detail="global-metadata.decrypted.dat 可交给 Il2CppDumper")
        else:
            result.update(state="格式异常", detail=f"decrypted header={header.hex()}")
    elif raw and raw.exists():
        header = raw.read_bytes()[:4]
        if header == magic:
            result.update(state="标准 metadata", detail="可直接交给 Il2CppDumper")
        else:
            result.update(state="加密/混淆", detail=f"header={header.hex()}，需先做内存 dump 或静态解密")
    else:
        result["detail"] = "未找到 global-metadata.dat；先从设备 APK 提取"
    return result


def _catalog_summary(artifacts):
    item = artifacts["protocol_catalog"]
    if not item["exists"]:
        return {"routes": 0, "protocol_types": 0, "types": 0, "string_literals": 0}
    try:
        payload = json.loads((ROOT / item["path"]).read_text(encoding="utf-8"))
        counts = payload.get("counts", {})
        return {
            "routes": int(counts.get("routes", 0)),
            "protocol_types": int(counts.get("protocol_types", 0)),
            "types": int(counts.get("types", 0)),
            "string_literals": int(counts.get("string_literals", 0)),
        }
    except Exception:
        return {"routes": 0, "protocol_types": 0, "types": 0, "string_literals": 0}


def _coverage(report):
    stages = 0
    template_path = ROOT / "backend" / "data" / "player_template.json"
    try:
        payload = json.loads(template_path.read_text(encoding="utf-8"))
        user = payload.get("user", payload)
        stages = len(user.get("dungeon", {}).get("stages", {}))
    except Exception:
        stages = 0
    return {
        "stages": stages,
        "shop_endpoints": 13,
        "pay_endpoints": 10,
        "reward_endpoints": 10,
        "persistence_checks": 6,
        "mail_endpoints": 5,
        "account_admin_modules": 5,
        "update_controls": 3,
        "alipay_mock": True,
        "tests": report["tests"] if report else 0,
        "tests_passed": report["passed"] if report else 0,
    }


def _gates(metadata, artifacts, report):
    lib_ok = artifacts["libil2cpp"]["exists"] and artifacts["libil2cpp"]["valid"]
    raw_ok = artifacts["raw_metadata"]["exists"]
    decrypted_ok = metadata["state"] in ("已解密", "标准 metadata")
    dump_ok = (
        artifacts["dump_cs"]["exists"]
        and artifacts["stringliteral"]["exists"]
        and artifacts["script_json"]["exists"]
        and artifacts["il2cpp_header"]["exists"]
    )
    tests_ok = bool(report) and report["tests"] > 0 and not report["failed"] and not report["errors"]
    return [
        {
            "key": "artifacts",
            "label": "客户端 IL2CPP 工件",
            "state": "done" if lib_ok and raw_ok else "blocked",
            "detail": "libil2cpp.so + global-metadata.dat 已就位" if lib_ok and raw_ok else (artifacts["libil2cpp"]["detail"] or "缺少 libil2cpp.so 或 metadata"),
        },
        {
            "key": "metadata",
            "label": "metadata 解密 / 内存 dump",
            "state": "done" if decrypted_ok else "blocked",
            "detail": metadata["detail"],
        },
        {
            "key": "dump",
            "label": "Il2CppDumper 导出",
            "state": "done" if dump_ok else "pending",
            "detail": (
                "dump.cs + stringliteral.json + script.json + il2cpp.h 已生成"
                if dump_ok
                else "protocol_catalog.json 已生成；完整 dump.cs 等待 CodeRegistration/MetadataRegistration"
                if artifacts["protocol_catalog"]["exists"]
                else "等待 protocol_catalog.json / dump.cs / stringliteral.json"
            ),
        },
        {
            "key": "tests",
            "label": "无头协议测试",
            "state": "done" if tests_ok else ("failed" if report and (report["failed"] or report["errors"]) else "pending"),
            "detail": report_summary(report),
        },
        {
            "key": "device",
            "label": "真机日志联调",
            "state": "manual",
            "detail": "仅阶段末执行：看 request_logs 与 adb logcat，不截图",
        },
    ]


def protocol_report():
    if not JUNIT_PATH.exists():
        return None
    try:
        root = ET.parse(JUNIT_PATH).getroot()
    except Exception:
        return None
    cases = []
    for node in root.iter("testcase"):
        status = "passed"
        message = ""
        for tag in ("failure", "error", "skipped"):
            child = node.find(tag)
            if child is not None:
                status = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                message = (child.get("message") or child.text or "").strip().splitlines()[0][:300]
                break
        cases.append(
            {
                "name": node.get("name", ""),
                "classname": node.get("classname", ""),
                "time": float(node.get("time", "0") or 0),
                "status": status,
                "message": message,
            }
        )
    failures = sum(1 for case in cases if case["status"] == "failed")
    errors = sum(1 for case in cases if case["status"] == "error")
    skipped = sum(1 for case in cases if case["status"] == "skipped")
    passed = sum(1 for case in cases if case["status"] == "passed")
    return {
        "tests": len(cases),
        "passed": passed,
        "failed": failures,
        "errors": errors,
        "skipped": skipped,
        "time": round(sum(case["time"] for case in cases), 3),
        "mtime": datetime.fromtimestamp(JUNIT_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "cases": cases,
    }


def protocol_matrix(report=None):
    report = report or protocol_report() or {"cases": []}
    by_name = {case["name"]: case for case in report.get("cases", [])}
    rows = []
    for endpoint in PROTOCOL_ENDPOINTS:
        case = by_name.get(endpoint["test"])
        rows.append({**endpoint, "status": case["status"] if case else "not_run", "message": case["message"] if case else ""})
    return rows


def report_summary(report):
    if not report:
        return "未运行"
    if report["failed"] or report["errors"]:
        return f"失败 {report['failed'] + report['errors']} / {report['tests']}"
    if report["skipped"]:
        return f"通过 {report['passed']} / {report['tests']}（跳过 {report['skipped']}）"
    return f"通过 {report['passed']} / {report['tests']}"


def protocol_tests_running():
    if not TEST_LOCK_PATH.exists():
        return False
    with open(TEST_LOCK_PATH, "w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        finally:
            try:
                fcntl.flock(lock, fcntl.LOCK_UN)
            except Exception:
                pass
    return False


def collect_status(force=False):
    now = time.time()
    if not force and _CACHE["data"] is not None and now - _CACHE["ts"] < 5:
        return _CACHE["data"]
    services = _service_rows()
    report = protocol_report()
    artifacts = {key: _artifact_row(key, pattern) for key, pattern in ARTIFACT_PATTERNS.items()}
    catalog = _catalog_summary(artifacts)
    metadata = _metadata_status()
    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "静态逆向 + 无头协议测试",
        "screenshot_policy": "截图仅作历史证据，不作为开发或验收手段",
        "services": services,
        "backend": _backend_status(services),
        "adb": _adb_status(detailed=force),
        "sdk_gateway": _sdk_gateway_status(),
        "metadata": metadata,
        "artifacts": artifacts,
        "catalog": catalog,
        "coverage": _coverage(report),
        "gates": _gates(metadata, artifacts, report),
        "tests_running": protocol_tests_running(),
        "report": report,
        "report_summary": report_summary(report),
        "protocol_matrix": protocol_matrix(report),
        "test_log_tail": _tail(TEST_LOG_PATH, 60),
        "watchdog_tail": _tail(WATCHDOG_LOG_PATH, 40),
    }
    _CACHE.update(ts=now, data=data)
    return data


def _tail(path, lines):
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    return "\n".join(content[-lines:])


def run_protocol_tests():
    LOGS.mkdir(parents=True, exist_ok=True)
    lock_path = TEST_LOCK_PATH
    with open(lock_path, "w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"rc": 1, "output": "已有一轮协议测试在运行", "report": protocol_report()}
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "tests/protocol",
            "-q",
            "--tb=short",
            f"--junitxml={JUNIT_PATH}",
        ]
        try:
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=240, check=False)
            output = (proc.stdout + proc.stderr).strip()
            rc = proc.returncode
        except subprocess.TimeoutExpired as exc:
            output = f"协议测试超时: {exc}"
            rc = 124
        TEST_LOG_PATH.write_text(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] rc={rc}\n{output}\n",
            encoding="utf-8",
        )
        return {"rc": rc, "output": output, "report": protocol_report()}
