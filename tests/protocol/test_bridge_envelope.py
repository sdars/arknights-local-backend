import json
import ssl
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest


BRIDGE_URL = "https://127.0.0.1:4443"
EXPECTED_UID = "10000000"


def _request(path, body=None):
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    request = Request(BRIDGE_URL + path, headers=headers, data=data)
    try:
        with urlopen(request, context=ssl._create_unverified_context(), timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError) as exc:
        pytest.skip(f"TLS 反向层未运行: {exc}")


def _data(envelope):
    assert envelope["status"] == 0
    assert envelope["type"] == "A"
    assert isinstance(envelope["data"], str)
    return json.loads(envelope["data"])


def test_bridge_sdk_envelope_and_stale_token_self_heal():
    for token in ("lab_manual_token", "__ark_stale_token__"):
        basic = _request("/user/info/v1/basic?" + urlencode({"token": token}))
        assert _data(basic)["identityNum"] == EXPECTED_UID

    grant = _request(
        "/user/oauth2/v2/grant",
        {"token": "__ark_stale_token__", "type": 0, "appCode": "7318def77669979d"},
    )
    payload = _data(grant)
    assert payload["uid"] == EXPECTED_UID
    assert payload["token"] == "lab_manual_token"
    assert payload["code"] == "lab_manual_token"
