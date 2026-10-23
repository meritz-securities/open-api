"""연속조회 값은 명세가 쿼리스트링으로 선언한 API 에서는 쿼리로만 보낸다.

보유종목의 연속조회 키는 공백이 100개 넘게 이어진다. 이 키를 HTTP 헤더로 보내면
방화벽이 요청을 막아 HTML 이 돌아온다.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MERITZ_CATALOG", str(ROOT / "meritz" / "data" / "catalog.json"))
os.environ.setdefault("MERITZ_NO_ENV_FILE", "1")

from meritz.core.catalog import load_catalog  # noqa: E402
from meritz.core.client import PAGE_MORE, PAGING_OK, ApiClient, paginate  # noqa: E402

CAT = load_catalog()


class _FakeTokens:
    def get(self):
        return "TOKEN"


class _FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.requests = []

    def call(self, api, params, **kw):
        self.requests.append(dict(params))
        return self.pages[min(len(self.requests) - 1, len(self.pages) - 1)]


def _page(rows, rsp_cd, key=None):
    body = {"rsp_cd": rsp_cd, "data": rows}
    if key is not None:
        body["tr_cont_key"] = key
    return {"ok": True, "body": body}


def test_declared_continuation_values_are_not_sent_as_headers():
    api = CAT.get("holdings")
    assert {p["name"] for p in api.params if p.get("location") == "query"} >= {"tr_cont", "tr_cont_key"}
    client = ApiClient.__new__(ApiClient)
    client.tokens = _FakeTokens()
    h = client.headers(api, {"tr_cont": "1", "tr_cont_key": "K" + " " * 100})
    assert "tr_cont" not in h and "tr_cont_key" not in h
    q = api.query_params({"tr_cont": "1", "tr_cont_key": "K" + " " * 100})
    assert q["tr_cont"] == "1" and q["tr_cont_key"].startswith("K ")


def test_holdings_paging_is_allowed():
    assert {"holdings", "ovs_holdings"} <= set(PAGING_OK)


def test_paginate_walks_holdings_in_pages_of_thirty():
    api = CAT.get("holdings")
    client = _FakeClient([_page([{"n": i} for i in range(30)], PAGE_MORE, "A" + " " * 100),
                          _page([{"n": i} for i in range(30, 42)], "5766")])
    out = paginate(client, api, {})
    assert out["pages"] == 2 and len(out["rows"]) == 42 and out["truncated"] is False
    assert client.requests[1]["tr_cont_key"].endswith(" " * 100), "공백을 줄이거나 자르면 안 됩니다"
