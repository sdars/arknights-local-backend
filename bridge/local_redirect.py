import http.client as http_client
import json
import os
from urllib.parse import quote

from mitmproxy import http as mhttp

BACKEND_HOST = os.environ.get("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8443"))
TOKEN = os.environ.get("ARK_TOKEN", "lab_manual_token")
UID = os.environ.get("ARK_UID", "10000000")


def backend(method, path, body=None, headers=None):
    conn = http_client.HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=10)
    h = {"Host": f"{BACKEND_HOST}:{BACKEND_PORT}"}
    if headers:
        h.update(headers)
    conn.request(method, path, body=body, headers=h)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def make_resp(status, body, ctype="application/json; charset=utf-8"):
    return mhttp.Response.make(status, body, {"Content-Type": ctype})


def json_body(body):
    try:
        obj = json.loads(body)
        return obj if isinstance(obj, dict) else None
    except (TypeError, ValueError):
        return None


def sdk_wrap(obj=None, status=0, msg="OK", data=None):
    """Return the HGSDK envelope; its data field is always a JSON string."""
    if obj is not None:
        status = obj.get("status", status)
        msg = obj.get("msg", msg)
        data = obj.get("data", data)
    if data is None:
        data = {}
    if not isinstance(data, str):
        data = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    envelope = {"status": int(status), "msg": str(msg or "OK"), "type": "A", "data": data}
    return json.dumps(envelope, ensure_ascii=False).encode()


def sdk_business_ok(status, body):
    if status != 200:
        return False
    obj = json_body(body)
    try:
        return bool(obj and int(obj.get("status", 1)) == 0 and obj.get("data") is not None)
    except (TypeError, ValueError):
        return False


def basic_response(path):
    status, body = backend("GET", path)
    if not sdk_business_ok(status, body) and TOKEN:
        fallback = "/user/info/v1/basic?token=" + quote(TOKEN, safe="")
        fallback_status, fallback_body = backend("GET", fallback)
        if sdk_business_ok(fallback_status, fallback_body):
            print("BACKEND basic token self-heal")
            return make_resp(200, sdk_wrap(json_body(fallback_body)))
    obj = json_body(body)
    if status == 200 and obj is not None:
        return make_resp(200, sdk_wrap(obj))
    return make_resp(status, body)


def grant_response(path, raw_body):
    headers = {"Content-Type": "application/json"}
    status, body = backend("POST", path, raw_body, headers)
    if not sdk_business_ok(status, body):
        try:
            fallback_obj = json.loads(raw_body or b"{}")
            if not isinstance(fallback_obj, dict):
                fallback_obj = {}
        except (TypeError, ValueError):
            fallback_obj = {}
        fallback_obj["token"] = TOKEN
        fallback_body = json.dumps(fallback_obj, separators=(",", ":")).encode()
        fallback_status, body_candidate = backend("POST", path, fallback_body, headers)
        if sdk_business_ok(fallback_status, body_candidate):
            status, body = fallback_status, body_candidate
            print("BACKEND grant token self-heal")
    obj = json_body(body)
    if status != 200 or obj is None:
        return make_resp(status, body)
    data = obj.get("data", {})
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            data = {}
    if not isinstance(data, dict):
        data = {}
    data.update({"code": TOKEN, "token": TOKEN, "uid": UID})
    obj["data"] = data
    return make_resp(200, sdk_wrap(obj))


def request(flow: mhttp.HTTPFlow) -> None:
    host = flow.request.pretty_host
    path = flow.request.path
    sni = getattr(flow.client_conn, "sni", "") or ""

    if (host == "openapi.alipay.com" or sni == "openapi.alipay.com") and path.startswith("/gateway.do"):
        order_id = flow.request.query.get("out_trade_no", "")
        target = f"http://127.0.0.1:8443/pay/success?orderId={quote(order_id)}"
        flow.response = mhttp.Response.make(
            302,
            b"",
            {"Location": target, "Content-Type": "text/plain; charset=utf-8"},
        )
        print(f"MOCK alipay gateway -> {target}")
        return

    # In reverse-proxy mode pretty_host is the rewritten upstream host, so
    # SDK routes must be selected by path rather than by the original Host.
    if path.startswith("/user/info/v1/basic"):
        flow.response = basic_response(path)
        print("BACKEND basic ok")
        return

    if path.startswith("/user/info/v1/update_agreement"):
        flow.response = make_resp(200, sdk_wrap())
        print("MOCK update_agreement")
        return

    if path.endswith("/user/oauth2/v2/grant"):
        flow.response = grant_response(path, flow.request.get_content())
        print("BACKEND grant ok")
        return

    if host == "game-config.hypergryph.com" and path.endswith("/ak_sdk_config"):
        flow.response = make_resp(200, b'{"report_device_info":10000}')
        return

    if host in ("game-config.hypergryph.com", "ak-conf.hypergryph.com", "launcher.hypergryph.com"):
        status, body = backend(flow.request.method, path)
        flow.response = make_resp(status, body)
        print("BACKEND", host, path, status, len(body))
        return

    if host == "ak-webview.hypergryph.com" and path.startswith("/api/gate/meta"):
        status, body = backend("GET", "/api/gate/meta/Android")
        flow.response = make_resp(status, body)
        return


def response(flow: mhttp.HTTPFlow) -> None:
    path = flow.request.path
    if path.startswith("/app/v1/config") and flow.response:
        try:
            obj = json.loads(flow.response.get_text(strict=False))
            app = obj.setdefault("data", {}).setdefault("app", {})
            app["enableAutoLogin"] = True
            app["enableAuthenticate"] = False
            app["enableAntiAddiction"] = False
            app["enablePaidApp"] = False
            body = json.dumps(obj, ensure_ascii=False).encode()
            flow.response.set_content(body)
            flow.response.headers["content-length"] = str(len(body))
            print("PATCH app config enableAutoLogin=true")
        except Exception as exc:
            print("PATCH config failed:", exc)

