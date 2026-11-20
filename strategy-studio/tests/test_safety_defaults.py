"""안전 기본값이 회귀하지 않게 잠근다.

모의계좌가 없다. 앱키에 묶인 계좌는 언제나 실계좌다. 이 서버는 AI 에이전트가
스스로 주문을 낼 수 있는 경로라, 기본값이 유일한 완충재다.

여기서 보는 것은 셋이다.
  ① 아무것도 설정하지 않으면 조회 전용인가
  ② 빈 값·공백·알아볼 수 없는 값에서 안전한 쪽으로 떨어지는가
  ③ 게이트가 도구 계층이 아니라 라이브러리(ApiClient.call) 안에 있는가
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MERITZ_NO_ENV_FILE", "1")
sys.path.insert(0, str(ROOT))

from meritz import safety  # noqa: E402
from meritz.catalog import load_catalog  # noqa: E402
from meritz.client import ApiClient, MeritzError  # noqa: E402
from meritz.config import Settings, settings  # noqa: E402
from meritz.safety import is_state_changing  # noqa: E402

CAT = load_catalog()
ORDER = CAT.get("orders_buy")
QUOTE = CAT.get("market_prices")


# ------------------------------------------------- 안전 스위치 기본값
def test_module_default_is_read_only():
    """진입점(server.py)이 아니라 **모듈 기본값**이 닫혀 있어야 한다.

    server.py 의 set_read_only_default(True) 에만 기대면, 라이브러리로
    직접 임포트해 쓰는 경로가 서버 경로보다 덜 안전해진다.
    """
    import meritz.config as cfg
    assert cfg._READ_ONLY_DEFAULT == "1"


def test_default_is_read_only(monkeypatch):
    monkeypatch.delenv("MERITZ_READ_ONLY", raising=False)
    assert settings().read_only is True


def test_dataclass_default_is_read_only():
    """환경변수를 거치지 않고 Settings 를 직접 만들어도 닫혀 있어야 한다."""
    assert Settings().read_only is True


@pytest.mark.parametrize("value", ["", "   ", "yes please", "참", "1 "])
def test_unreadable_switch_value_falls_back_to_safe(monkeypatch, value):
    """안전 스위치가 오타 하나로 열리면 안 된다."""
    monkeypatch.setenv("MERITZ_READ_ONLY", value)
    assert settings().read_only is True, f"{value!r} 에서 열렸다"


def test_env_can_open_orders(monkeypatch):
    """명시적으로 열 수는 있어야 한다 — 그렇지 않으면 주문을 쓸 수 없다."""
    monkeypatch.setenv("MERITZ_READ_ONLY", "0")
    assert settings().read_only is False


# ------------------------------------------------- 주문 경로 게이트
def test_orders_are_blocked_in_read_only():
    c = ApiClient(Settings(app_key="k", app_secret="s", read_only=True))
    with pytest.raises(MeritzError) as e:
        c.call(ORDER, {})
    assert e.value.code == "READ_ONLY"


def test_orders_need_a_confirm_token_even_from_the_library():
    """게이트는 라이브러리 계층에 있어야 한다.

    도구 계층에만 두면 ApiClient 를 직접 임포트해 쓰는 코드에서 주문이
    무확인으로 나간다.
    """
    c = ApiClient(Settings(app_key="k", app_secret="s", read_only=False))
    with pytest.raises(MeritzError) as e:
        c.call(ORDER, _order_body())
    assert e.value.code == "NEEDS_CONFIRMATION"
    assert "confirm_token" in (e.value.body or {})


def test_a_valid_token_passes_the_gate():
    s = Settings(app_key="k", app_secret="s", read_only=False)
    c = ApiClient(s)
    body = ORDER.body_params(_order_body())
    pv = safety.preview(ORDER, body, s.base_url, s.app_key)
    # 게이트만 본다 — 통과하면 예외가 없다. 전송은 하지 않는다.
    c.require_confirmation(ORDER, {**_order_body(),
                                   "confirm_token": pv["confirm_token"]})


def test_the_token_is_single_use():
    s = Settings(app_key="k", app_secret="s", read_only=False)
    c = ApiClient(s)
    params = _order_body()
    pv = safety.preview(ORDER, ORDER.body_params(params), s.base_url, s.app_key)
    params["confirm_token"] = pv["confirm_token"]
    c.require_confirmation(ORDER, params)
    with pytest.raises(MeritzError) as e:
        c.require_confirmation(ORDER, params)
    assert e.value.code == "NEEDS_CONFIRMATION"


def test_queries_do_not_need_a_token():
    c = ApiClient(Settings(app_key="k", app_secret="s", read_only=True))
    c.require_confirmation(QUOTE, {"iscd": "005930"})


def _order_body() -> dict:
    return {"iscd": "A005930", "odqt": "1", "oder_unpr": "50000",
            "oder_cls_code": "01", "oder_cond_cls_code": "0",
            "orgl_oder_no": "0", "whol_rctf_cncl_yn": "N",
            "warn_cnfr_yn": "N", "exch_kind_code": "01"}


def test_token_revoke_is_gated_but_issue_is_not():
    """토큰 폐기는 조회 전용에서 막혀야 한다.

    같은 앱키로 발급된 토큰은 하나뿐이라, 폐기하면 다른 프로세스·세션이
    쓰던 토큰까지 끊긴다. oauth2 카테고리를 통째로 면제했더니 시험 삼아
    부를 수 있는 자리가 열려 있었다. 발급은 모든 호출의 전제라 면제를
    유지한다.
    """
    assert is_state_changing(CAT.get("oauth2_revoke")) is True
    assert is_state_changing(CAT.get("oauth2_token")) is False


# ------------------------------------------------------------- paginate()
#
# 여기가 틀리면 건수가 조용히 모자라게 돌아온다. 합계로 쓰면 틀린 수치가
# 그대로 화면에 나가는데, 예외도 오류 코드도 없어 알아챌 방법이 없다.
class _FakeClient:
    """호출 순서대로 미리 정한 응답을 돌려준다. 네트워크를 쓰지 않는다."""

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


def test_paginate_walks_until_the_server_stops():
    from meritz.client import PAGE_MORE, paginate

    api = CAT.get("transactions")
    client = _FakeClient([_page([{"n": 1}], PAGE_MORE, "K1"),
                          _page([{"n": 2}], PAGE_MORE, "K2"),
                          _page([{"n": 3}], "0000")])
    out = paginate(client, api, {"from": "20260101"})
    assert out["pages"] == 3
    assert [r["n"] for r in out["rows"]] == [1, 2, 3]
    assert out["truncated"] is False


def test_paginate_sends_the_continuation_key_untouched():
    """키에 붙어 오는 오른쪽 공백을 잘라내면 서버가 다른 구간을 준다."""
    from meritz.client import PAGE_MORE, paginate

    api = CAT.get("transactions")
    client = _FakeClient([_page([{"n": 1}], PAGE_MORE, "ABC   "),
                          _page([{"n": 2}], "0000")])
    paginate(client, api, {"from": "20260101"})
    assert client.requests[1]["tr_cont_key"] == "ABC   "
    assert client.requests[1]["tr_cont"] == "1"


def test_paginate_stops_when_the_key_never_changes():
    """같은 키를 다시 보내면 같은 구간이 영원히 돌아온다."""
    from meritz.client import PAGE_MORE, paginate

    api = CAT.get("transactions")
    client = _FakeClient([_page([{"n": 1}], PAGE_MORE, "SAME")] * 5)
    out = paginate(client, api, {"from": "20260101"})
    assert out["pages"] == 2, "키가 그대로면 두 번째에서 멈춰야 합니다"
    assert out["truncated"] is True
    assert out["note"], "끝까지 받지 못했으면 이유를 남겨야 합니다"


def test_paginate_marks_truncated_at_the_page_limit():
    from meritz.client import PAGE_MORE, paginate

    api = CAT.get("transactions")
    pages = [_page([{"n": i}], PAGE_MORE, f"K{i}") for i in range(10)]
    client = _FakeClient(pages)
    out = paginate(client, api, {"from": "20260101"}, max_pages=3)
    assert out["pages"] == 3
    assert out["truncated"] is True


def test_paginate_refuses_apis_that_do_not_support_it():
    """명세에 tr_cont 가 있어도 다음 구간이 오지 않는 API 가 있다."""
    from meritz.client import MeritzError, paginate

    client = _FakeClient([_page([], "0000")])
    with pytest.raises(MeritzError) as e:
        paginate(client, CAT.get("market_investors"), {})
    assert e.value.code == "PAGING_UNSUPPORTED"
    assert client.requests == [], "지원하지 않는 API 는 호출조차 하지 않아야 합니다"


def test_frozen_binary_reads_env_beside_itself():
    """단일 실행 파일은 자기 옆의 .env 를 가장 먼저 읽는다.

    이 경로가 있으면 앱키를 명령줄에 적지 않아도 되고, 셸 기록과 프로세스
    목록에 남지 않는다. README 가 한동안 "단일 실행 파일은 .env 를 읽지
    않는다"고 반대로 안내해, 이용자가 가장 안전한 방법을 쓰지 못했다.
    """
    import pathlib
    import sys

    from meritz import config

    frozen, exe = getattr(sys, "frozen", None), sys.executable
    try:
        sys.frozen = True                       # type: ignore[attr-defined]
        sys.executable = "/tmp/_probe/meritz-mcp"
        first = config._env_file_candidates()[0]
    finally:
        sys.executable = exe
        if frozen is None:
            del sys.frozen                      # type: ignore[attr-defined]
        else:
            sys.frozen = frozen                 # type: ignore[attr-defined]

    assert first == pathlib.Path("/tmp/_probe/.env").resolve(), \
        f"실행 파일 옆 .env 가 1순위여야 합니다 (받은 값: {first})"


def test_readme_does_not_deny_the_env_file():
    """README 가 코드와 반대로 안내하면 걸린다."""
    import pathlib

    text = (pathlib.Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    assert ".env`를 읽지 않으니" not in text, "README 가 .env 를 읽지 않는다고 적고 있습니다"


# ------------------------------------------------------------- 다중 계좌
#
# 앱키 하나에 계좌 하나가 묶인다. 앱키를 여러 개 등록하면 앱키 목록이 곧
# 계좌 목록이 되고, 어느 계좌로 나가는지를 틀리면 되돌릴 수 없다.
def _two_profiles(read_only=False):
    from meritz.config import Settings

    return [Settings(label="주력", app_key="k1", app_secret="s1", read_only=read_only),
            Settings(label="연금", app_key="k2", app_secret="s2", read_only=read_only)]


def test_orders_refuse_to_pick_an_account_for_you():
    """계좌가 여럿이면 주문은 기본값으로 나가지 않는다."""
    from meritz.accounts import Registry
    from meritz.client import MeritzError

    reg = Registry(_two_profiles())
    with pytest.raises(MeritzError) as e:
        reg.resolve(None, require_explicit=True)
    assert e.value.code == "ACCOUNT_REQUIRED"
    # 조회는 기본을 허용한다 — 틀려도 다시 보면 된다
    assert reg.resolve(None, require_explicit=False).s.label == "주력"


def test_account_label_must_match_exactly():
    """비슷한 이름으로 추측하면 다른 계좌에 주문이 나간다."""
    from meritz.accounts import Registry
    from meritz.client import MeritzError

    reg = Registry(_two_profiles())
    for wrong in ("주", "주력 ", "연금계좌", "yeongeum", ""):
        if wrong == "":
            continue
        with pytest.raises(MeritzError) as e:
            reg.resolve(wrong, require_explicit=False)
        assert e.value.code == "UNKNOWN_ACCOUNT", f"{wrong!r} 가 통과했습니다"


def test_duplicate_account_labels_refuse_to_start():
    """라벨이 겹치면 조용히 하나를 덮지 않고 세우지 않는다."""
    from meritz.accounts import Registry
    from meritz.config import Settings

    dupes = [Settings(label="같음", app_key="k1", app_secret="s1"),
             Settings(label="같음", app_key="k2", app_secret="s2")]
    with pytest.raises(Exception):
        Registry(dupes)


def test_each_account_gets_its_own_token_cache():
    """토큰 캐시가 섞이면 A 계좌 토큰으로 B 계좌를 조회하게 된다."""
    from meritz.accounts import Registry

    reg = Registry(_two_profiles())
    a = reg.resolve("주력", require_explicit=True)
    b = reg.resolve("연금", require_explicit=True)
    assert a.tokens is not b.tokens
    assert a.tokens._cache_key != b.tokens._cache_key


def test_confirm_token_does_not_cross_accounts():
    """A 계좌로 받은 미리보기 토큰은 B 계좌 주문에 쓰일 수 없다."""
    from meritz.safety import check_confirm, issue_confirm_token

    body, url = {"iscd": "A005930", "odqt": 1}, "https://example.invalid"
    tok = issue_confirm_token("orders_buy", body, url, "k1")
    assert check_confirm("orders_buy", body, tok, url, "k2") is not None, \
        "다른 앱키로 통과하면 안 됩니다"


def test_a_profile_cannot_open_what_the_global_switch_closed():
    """소액 계좌 하나 열자고 주력 계좌까지 열리면 안 된다."""
    import os

    from meritz.config import load_profiles

    keep = dict(os.environ)
    try:
        os.environ.update({
            "MERITZ_NO_ENV_FILE": "1", "MERITZ_READ_ONLY": "1",
            "MERITZ_APP_KEY": "k1", "MERITZ_APP_SECRET": "s1",
            "MERITZ_APP_KEY_2": "k2", "MERITZ_APP_SECRET_2": "s2",
            "MERITZ_LABEL_2": "연금", "MERITZ_READ_ONLY_2": "0"})
        for st in load_profiles():
            assert st.read_only is True, f"{st.label} 이 전역 잠금을 뚫었습니다"
    finally:
        os.environ.clear(); os.environ.update(keep)


def test_unsubstituted_placeholders_are_treated_as_empty():
    """설정 칸을 비우면 클라이언트가 자리표시자를 그대로 넘긴다.

    Claude Desktop 은 값이 없으면 치환을 건너뛰고 "${user_config.label_2}"
    같은 문자열을 환경변수로 보낸다. 그대로 믿으면 이름 자리에 자리표시자가
    박히고, 앱키 자리에서는 값이 있는 것처럼 보여 **유령 계좌**가 생긴다.
    """
    import os

    from meritz.config import load_profiles

    keep = dict(os.environ)
    try:
        for k in list(os.environ):
            if k.startswith("MERITZ_"):
                del os.environ[k]
        os.environ.update({
            "MERITZ_NO_ENV_FILE": "1",
            "MERITZ_APP_KEY": "k1", "MERITZ_APP_SECRET": "s1",
            "MERITZ_LABEL": "${user_config.label}",
            "MERITZ_APP_KEY_2": "${user_config.app_key_2}",
            "MERITZ_APP_SECRET_2": "${user_config.app_secret_2}",
            "MERITZ_LABEL_2": "${user_config.label_2}"})
        profiles = load_profiles()
        assert len(profiles) == 1, f"유령 계좌가 생겼습니다: {[p.label for p in profiles]}"
        assert "${" not in profiles[0].label, "자리표시자가 계좌 이름이 됐습니다"
        assert profiles[0].app_key == "k1"
    finally:
        os.environ.clear(); os.environ.update(keep)


def test_unnamed_accounts_get_labels_a_person_can_say():
    """이름을 안 적어도 대화에서 고를 수 있는 이름이어야 한다."""
    import os

    from meritz.config import load_profiles

    keep = dict(os.environ)
    try:
        for k in list(os.environ):
            if k.startswith("MERITZ_"):
                del os.environ[k]
        os.environ.update({
            "MERITZ_NO_ENV_FILE": "1",
            "MERITZ_APP_KEY": "k1", "MERITZ_APP_SECRET": "s1",
            "MERITZ_APP_KEY_2": "k2", "MERITZ_APP_SECRET_2": "s2"})
        labels = [p.label for p in load_profiles()]
        assert labels == ["1번계좌", "2번계좌"], labels
    finally:
        os.environ.clear(); os.environ.update(keep)


def test_half_filled_account_says_what_is_missing():
    """키만 넣고 시크릿을 빠뜨리면 게이트웨이는 EGW00103 으로 거절한다.

    그 메시지로는 "앱키가 틀렸나" 싶어 엉뚱한 곳을 보게 된다. 보내기 전에
    무엇이 비었는지 말해 준다.
    """
    from meritz.accounts import Registry
    from meritz.client import MeritzError
    from meritz.config import Settings

    reg = Registry([Settings(label="정상", app_key="k1", app_secret="s1"),
                    Settings(label="키만", app_key="k2"),
                    Settings(label="시크릿만", app_secret="s3")])
    for label, want in (("키만", "App Secret"), ("시크릿만", "App Key")):
        with pytest.raises(MeritzError) as e:
            reg.resolve(label, require_explicit=True)
        assert e.value.code == "NO_CREDENTIALS"
        assert want in str(e.value), f"{label}: 무엇이 비었는지 말하지 않습니다"
    assert reg.resolve("정상", require_explicit=True).s.label == "정상"


def test_manifest_groups_each_account_together():
    """설정 화면에서 이름·키·시크릿이 계좌별로 붙어 있어야 짝을 안 섞는다."""
    import json
    import pathlib

    d = json.loads((pathlib.Path(__file__).resolve().parent.parent / "manifest.json")
                   .read_text(encoding="utf-8"))
    keys = list(d["user_config"])
    for sfx in ("", "_2", "_3"):
        trio = [f"label{sfx}", f"app_key{sfx}", f"app_secret{sfx}"]
        pos = [keys.index(k) for k in trio]
        assert pos == sorted(pos) and pos[-1] - pos[0] == 2, \
            f"계좌{sfx or '1'} 의 칸이 흩어져 있습니다: {pos}"
    assert keys[-1] == "read_only", "전체 설정은 계좌 칸 뒤에 와야 합니다"


# ---------------------------------------------------- 연속조회 값은 쿼리스트링으로만
#
# 보유종목의 연속조회 키는 공백이 100개 넘게 이어진다. 이 키를 HTTP 헤더로 보내면
# 방화벽이 요청을 막아 HTML 이 돌아온다. 명세는 이 값을 쿼리스트링으로 선언하므로
# 선언된 API 는 쿼리로만 보낸다.
class _FakeTokens:
    def get(self):
        return "TOKEN"


def _client_without_network():
    client = ApiClient.__new__(ApiClient)
    client.tokens = _FakeTokens()
    return client


def test_declared_continuation_values_are_not_sent_as_headers():
    api = CAT.get("holdings")
    assert {p["name"] for p in api.params if p.get("location") == "query"} >= {"tr_cont", "tr_cont_key"}
    h = _client_without_network().headers(api, {"tr_cont": "1", "tr_cont_key": "K" + " " * 100})
    assert "tr_cont" not in h and "tr_cont_key" not in h
    q = api.query_params({"tr_cont": "1", "tr_cont_key": "K" + " " * 100})
    assert q["tr_cont"] == "1" and q["tr_cont_key"].startswith("K ")


def test_holdings_paging_is_allowed():
    from meritz.client import PAGING_OK
    assert {"holdings", "ovs_holdings"} <= set(PAGING_OK)


def test_paginate_walks_holdings_in_pages_of_thirty():
    from meritz.client import PAGE_MORE, paginate

    api = CAT.get("holdings")
    client = _FakeClient([_page([{"n": i} for i in range(30)], PAGE_MORE, "A" + " " * 100),
                          _page([{"n": i} for i in range(30, 42)], "5766")])
    out = paginate(client, api, {})
    assert out["pages"] == 2 and len(out["rows"]) == 42 and out["truncated"] is False
    assert client.requests[1]["tr_cont_key"].endswith(" " * 100), "공백을 줄이거나 자르면 안 됩니다"
