"""서버 로그에서 이 도구가 보낸 요청을 구별할 수 있는지 본다.

User-Agent 는 호출한 도구와 판 번호만 담는다. 사용자 이름·호스트명·MAC 은 넣지 않는다.
"""
from __future__ import annotations

import re
from unittest import mock

import requests

from meritz import __version__
from meritz.client import USER_AGENT, ApiClient, TokenManager
from meritz.config import Settings

# 판 번호는 PEP 440 — 베타는 0.5.0b1 처럼 프리릴리스 꼬리가 붙는다.
SHAPE = re.compile(r"^meritz-open-api-mcp/(\d+\.\d+\.\d+(?:[abrc]+\d+)?) \(python/\d+\.\d+; [a-z0-9]+\)$")


def test_user_agent_names_the_tool_and_its_version():
    found = SHAPE.match(USER_AGENT)
    assert found, USER_AGENT
    assert found.group(1) == __version__


def test_user_agent_carries_no_personal_identifiers():
    import getpass
    import socket
    low = USER_AGENT.lower()
    assert getpass.getuser().lower() not in low
    assert socket.gethostname().lower() not in low


def _capture(fn):
    """HTTPAdapter.send 에서 실제로 나가는 요청의 헤더를 잡는다."""
    seen = []

    def fake_send(self, request, **kw):
        seen.append(dict(request.headers))
        resp = requests.Response()
        resp.status_code = 200
        resp._content = b'{"access_token": "t", "expires_in": 3600, "rsp_cd": "0000", "data": []}'
        resp.headers["content-type"] = "application/json"
        resp.url = request.url
        return resp

    with mock.patch.object(requests.adapters.HTTPAdapter, "send", fake_send):
        fn()
    return seen


def test_token_request_carries_the_user_agent():
    tm = TokenManager(Settings(app_key="k", app_secret="s"))
    seen = _capture(tm.get)
    assert seen and seen[0].get("user-agent", seen[0].get("User-Agent")) == USER_AGENT


def test_api_requests_carry_the_user_agent():
    c = ApiClient(Settings(app_key="k", app_secret="s"))
    seen = _capture(lambda: c._http.get("https://example.invalid/x"))
    assert seen[0]["User-Agent"] == USER_AGENT
