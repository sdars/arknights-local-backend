import fcntl
import json
import os
import subprocess
import struct
import sys
import time
from datetime import datetime
from pathlib import Path
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
    "protocol_catalog": "backend/data/protocol_catalog.json",
}

ARTIFACT_LABELS = {
    "libil2cpp": "libil2cpp.so",
    "raw_metadata": "global-metadata.dat（原始）",
    "decrypted_metadata": "global-metadata.decrypted.dat",
    "dump_cs": "dump.cs",
    "stringliteral": "stringliteral.json",
    "script_json": "script.json",
    "protocol_catalog": "protocol_catalog.json",
}

_CACHE = {"ts": 0.0, "data": None}

PROTOCOL_ENDPOINTS = [
    {"stage": "SDK", "method": "GET", "path": "/app/v1/config", "test": "test_sdk_config", "desc": "应用配置"},
    {"stage": "SDK", "method": "GET", "path": "/general/v1/server_time", "test": "test_server_time", "desc": "服务器时间"},
    {"stage": "SDK", "method": "GET", "path": "/api/remote_config/1/prod/default/Android/network_config", "test": "test_network_config", "desc": "网络配置"},
    {"stage": "SDK", "method": "GET", "path": "/user/info/v1/basic", "test": "test_basic", "desc": "账号基础信息"},
    {"stage": "SDK", "method": "POST", "path": "/user/oauth2/v2/grant", "test": "test_grant", "desc": "OAuth grant"},
    {"stage": "SDK", "method": "POST", "path": "/u8/user/v1/getToken", "test": "test_u8_get_token", "desc": "U8 渠道 token"},
    {"stage": "SDK", "method": "POST", "path": "/u8/pay/getAllProductList", "test": "test_product_list", "desc": "商品列表"},
    {"stage": "登录", "method": "POST", "path": "/account/login", "test": "test_account_login", "desc": "游戏登录"},
    {"stage": "登录", "method": "POST", "path": "/account/syncData", "test": "test_sync_data", "desc": "玩家数据同步"},
    {"stage": "登录", "method": "POST", "path": "/account/syncStatus", "test": "test_sync_status", "desc": "在线状态同步"},
    {"stage": "战斗", "method": "POST", "path": "/quest/battleStart", "test": "test_battle_start", "desc": "开始战斗"},
    {"stage": "战斗", "method": "POST", "path": "/quest/battleFinish", "test": "test_battle_finish", "desc": "结束战斗"},
    {"stage": "后台", "method": "GET", "path": "/admin/login", "test": "test_admin_login_page", "desc": "管理后台登录页"},
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


def _adb_status():
    serial = _env_value("ADB_SERIAL", "")
    result = {
        "serial": serial,
        "state": "未配置",
        "reverse": [],
        "proxy": "",
        "https_forward": False,
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
    return result


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


def _gates(metadata, artifacts, report):
    lib_ok = artifacts["libil2cpp"]["exists"] and artifacts["libil2cpp"]["valid"]
    raw_ok = artifacts["raw_metadata"]["exists"]
    decrypted_ok = metadata["state"] in ("已解密", "标准 metadata")
    dump_ok = artifacts["dump_cs"]["exists"] and artifacts["stringliteral"]["exists"]
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
                "dump.cs + stringliteral.json 已生成"
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
        "adb": _adb_status(),
        "metadata": metadata,
        "artifacts": artifacts,
        "catalog": catalog,
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
