import http.client as http_client
import json
import os

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


def sdk_wrap(obj):
    data = obj.get("data", {})
    if not isinstance(data, str):
        data = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return json.dumps(
        {"status": 0, "msg": "OK", "type": "A", "data": data},
        ensure_ascii=False,
    ).encode()


def request(flow: mhttp.HTTPFlow) -> None:
    host = flow.request.pretty_host
    path = flow.request.path

    if host == "as.hypergryph.com" and path.startswith("/user/info/v1/basic"):
        status, body = backend("GET", path)
        if status == 200:
            try:
                flow.response = make_resp(200, sdk_wrap(json.loads(body)))
                print("BACKEND basic ok")
                return
            except Exception as exc:
                print("basic wrap fail:", exc)
        flow.response = make_resp(status, body)
        return

    if host == "as.hypergryph.com" and path.startswith("/user/info/v1/update_agreement"):
        flow.response = make_resp(200, b'{"status":0,"msg":"OK","type":"A"}')
        print("MOCK update_agreement")
        return

    if host == "as.hypergryph.com" and path.endswith("/user/oauth2/v2/grant"):
        status, body = backend(
            "POST", path, flow.request.get_content(), {"Content-Type": "application/json"}
        )
        if status == 200:
            try:
                obj = json.loads(body)
                data = obj.setdefault("data", {})
                if isinstance(data, str):
                    data = json.loads(data)
                data["code"] = TOKEN
                data["token"] = TOKEN
                data["uid"] = UID
                obj["data"] = data
                flow.response = make_resp(200, sdk_wrap(obj))
                print("BACKEND grant ok")
                return
            except Exception as exc:
                print("grant wrap fail:", exc)
        flow.response = make_resp(status, body)
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
    host = flow.request.pretty_host
    path = flow.request.path
    if host == "as.hypergryph.com" and path.startswith("/app/v1/config") and flow.response:
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

