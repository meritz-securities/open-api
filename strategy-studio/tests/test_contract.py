"""계약 검증. 네트워크 없이 도는 것만 담는다.

가장 중요한 것은 **주문이 확인 없이 나가지 않는 것**이다.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meritz import load_catalog  # noqa: E402
from meritz.accounts import Registry  # noqa: E402
from meritz.client import ApiClient, MeritzError, mac_address  # noqa: E402, _is_empty
from meritz.config import PROD, PROD_WS, settings  # noqa: E402
from meritz.safety import blocked, is_state_changing, preview  # noqa: E402
from meritz.tools import (  # noqa: E402
    API_DEFAULTS, API_HINT, CATEGORY_HINT, TOOL_OF, apply_defaults, describe, validate)

CAT = load_catalog()

# 운영이 아닌 임의의 서버를 뜻하는 자리표시자. 실재하는 사내 호스트를 쓰지 않는다.
OTHER = "https://example.invalid"


class CatalogTest(unittest.TestCase):
    def test_loaded_from_portal(self):
        self.assertIn("포털", CAT.source)
        self.assertGreater(len(CAT), 0)

    def test_rest_and_websocket_split(self):
        self.assertEqual(len(CAT.rest()) + len(CAT.websockets()), len(CAT))
        for a in CAT.websockets():
            self.assertTrue(a.path.startswith("/websocket/"), a.key)

    def test_keys_are_unique_and_safe(self):
        keys = [a.key for a in CAT]
        self.assertEqual(len(keys), len(set(keys)), "api_type 중복")
        for k in keys:
            self.assertRegex(k, r"^[a-z][a-z0-9_]*$", f"쓰기 불편한 api_type: {k}")

    def test_every_api_has_method_and_tr(self):
        for a in CAT:
            self.assertTrue(a["tr_id"], f"{a.key} tr_id 없음")
            self.assertIn(a["method"], ("GET", "POST", "SUBSCRIBE"), a.key)

    def test_param_location_matches_method(self):
        for a in CAT.rest():
            locs = {p.get("location") for p in a.params}
            if a.method == "GET":
                self.assertNotIn("body", locs, f"{a.key} GET 인데 body 파라미터")


class SafetyTest(unittest.TestCase):
    """상태변경이 하나라도 새면 실제 주문이 나간다."""

    def test_every_post_is_state_changing(self):
        """예외는 safety._EXEMPT_CATEGORIES 에 적힌 것뿐이어야 한다.

        oauth2 는 자기 세션의 토큰을 받고 버리는 호출이라 판정에서 뺀다.
        그 밖의 POST 가 하나라도 빠지면 무확인으로 나간다.
        """
        from meritz.safety import _EXEMPT_CATEGORIES
        missed = [a.key for a in CAT.rest()
                  if a.method == "POST" and not is_state_changing(a)
                  and (a["category"] or "").lower() not in _EXEMPT_CATEGORIES]
        self.assertEqual(missed, [], f"게이트를 빠져나가는 상태변경 API: {missed}")

    def test_the_only_exempt_category_is_oauth2(self):
        from meritz.safety import _EXEMPT_CATEGORIES
        self.assertEqual(set(_EXEMPT_CATEGORIES), {"oauth2"})

    def test_no_get_is_state_changing(self):
        wrong = [a.key for a in CAT.rest()
                 if a.method == "GET" and is_state_changing(a)]
        self.assertEqual(wrong, [], f"조회인데 상태변경으로 오판: {wrong}")

    def test_order_lookups_are_not_state_changing(self):
        for key in ("orders_history", "orders_estimate",
                    "ovs_orders_detail", "ovs_orders_today", "fx_exchanges_history"):
            a = CAT.get(key)
            if a:
                self.assertFalse(is_state_changing(a), key)

    def test_websocket_is_not_state_changing(self):
        for a in CAT.websockets():
            self.assertFalse(is_state_changing(a), a.key)

    def test_preview_does_not_leak_credentials(self):
        a = CAT.get("orders_buy")
        p = preview(a, {"iscd": "A005930"}, PROD)
        blob = json.dumps(p, ensure_ascii=False)
        self.assertNotIn("Bearer", blob)
        self.assertTrue(p["needs_confirmation"])


class ValidationTest(unittest.TestCase):
    def test_missing_required_is_caught(self):
        a = CAT.get("market_prices")
        self.assertTrue(validate(a, {"mrkt_div_code": "J"}))
        self.assertFalse(validate(a, {"mrkt_div_code": "J", "iscd": "005930"}))

    def test_unknown_param_is_caught(self):
        a = CAT.get("market_prices")
        problems = validate(a, {"mrkt_div_code": "J", "iscd": "005930", "zzz": 1})
        self.assertTrue(any("명세에 없는" in p for p in problems))

    def test_meta_keys_are_allowed(self):
        a = CAT.get("market_prices")
        self.assertFalse(validate(a, {"mrkt_div_code": "J", "iscd": "005930",
                                      "confirm": True, "tr_cont": "1"}))

    def test_corrected_required_params_are_enforced(self):
        """포털이 requireYn=N 으로 둔 필수값도 호출 전에 막아야 한다.

        예전에는 "포털 예시가 나쁘다" 는 것을 근거로 삼았는데, 포털 예시가
        고쳐지면 테스트가 통과하면서 정작 보호 장치는 검사되지 않는다.
        검사할 것은 예시가 아니라 corrections 의 actual_required 가
        실제로 검증에 걸리느냐다.
        """
        for key in ("ovs_market_prices", "ovs_market_orderbook"):
            a = CAT.get(key)
            self.assertIsNotNone(a, key)
            req = a.required
            self.assertTrue(req, f"{key} 에 필수 파라미터가 하나도 없다")
            full = {n: "x" for n in req}
            self.assertFalse(validate(a, full), f"{key} 전부 채웠는데 막혔다")
            for miss in req:
                partial = {k: v for k, v in full.items() if k != miss}
                self.assertTrue(validate(a, partial),
                                f"{key} 에서 {miss} 를 빼도 통과했다")


class ResponseJudgeTest(unittest.TestCase):
    def setUp(self):
        self.api = CAT.get("market_prices")

    def test_broken_rsp_cd_with_data_is_ok_with_warning(self):
        """손상 대응은 **알려진 API 에만** 적용된다. 목록을 넘겨야 통과한다."""
        r = ApiClient.judge(self.api, 200, {"rsp_cd": "\x00\x11", "data": {"x": 1}},
                            "u", {"market_prices"})
        self.assertTrue(r["ok"])
        self.assertIn("warning", r)
        self.assertFalse(r["verified"])

    def test_broken_rsp_cd_outside_whitelist_is_failure(self):
        """목록에 없으면 성공으로 넘기지 않는다 — 새 API 가 조용히 편승하지 못하게."""
        r = ApiClient.judge(self.api, 200, {"rsp_cd": "\x00\x11", "data": {"x": 1}}, "u")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "UNREADABLE_RESPONSE_CODE")

    def test_broken_rsp_cd_without_data_is_failure(self):
        r = ApiClient.judge(self.api, 200, {"rsp_cd": "\x00\x11", "rsp_msg": ""}, "u",
                            {"market_prices"})
        self.assertFalse(r["ok"])

    def test_business_error_is_failure(self):
        r = ApiClient.judge(self.api, 200,
                            {"rsp_cd": "IGW50004", "rsp_msg": "전문바디 오류"}, "u")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "BUSINESS_ERROR")

    def test_http_error_is_failure(self):
        self.assertFalse(ApiClient.judge(self.api, 500, {"rsp_cd": "0000"}, "u")["ok"])

    def test_order_accept_carries_note(self):
        r = ApiClient.judge(self.api, 200, {"rsp_cd": "0001"}, "u")
        self.assertTrue(r["ok"])
        self.assertIn("체결", r["note"])

    def test_paging_carries_note(self):
        r = ApiClient.judge(self.api, 200, {"rsp_cd": "5762", "data": []}, "u")
        self.assertIn("tr_cont", r["note"])


class EnvironmentTest(unittest.TestCase):
    def setUp(self):
        # 개발 편의용 .env 가 도메인을 덮으므로, 배포 기본값 확인 중에는 읽지 않는다.
        self.saved = {k: os.environ.get(k)
                      for k in ("MERITZ_BASE_URL", "MERITZ_WS_URL", "MERITZ_NO_ENV_FILE")}
        for k in self.saved:
            os.environ.pop(k, None)
        os.environ["MERITZ_NO_ENV_FILE"] = "1"

    def tearDown(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_production_is_the_shipped_default(self):
        s = settings()
        self.assertEqual(s.base_url, PROD)
        self.assertEqual(s.ws_url, PROD_WS)

    def test_base_url_override_wins(self):
        os.environ["MERITZ_BASE_URL"] = OTHER
        self.assertEqual(settings().base_url, OTHER)

    def test_env_variable_no_longer_switches_servers(self):
        """ENV 는 없앴다. 남아 있어도 운영 기본값이 흔들리지 않아야 한다."""
        os.environ["ENV"] = "dev"
        try:
            self.assertEqual(settings().base_url, PROD)
        finally:
            os.environ.pop("ENV", None)


class UsageHintTest(unittest.TestCase):
    """카탈로그 설명은 200자로 잘리므로, 정규장 종가 사용법은 따로 실어야 한다."""

    def test_candles_describe_tells_how_to_get_regular_session_close(self):
        text = describe(CAT.get("market_candles_days"), CAT)
        self.assertIn("정규장 종가", text)
        self.assertIn("'TJ'", text)
        self.assertIn("exch_use_yn='N'", text)

    def test_hint_is_only_on_the_api_it_is_about(self):
        text = describe(CAT.get("market_prices"), CAT)
        self.assertNotIn("사용 안내", text)

    def test_hint_apis_exist_in_catalog(self):
        # 카탈로그에서 API 이름이 바뀌면 안내가 조용히 사라진다. 없어지면 여기서 알린다.
        for key in API_HINT:
            self.assertIsNotNone(CAT.get(key), key)
        for category in CATEGORY_HINT:
            self.assertTrue(any(a["category"] == category for a in CAT.rest()), category)


class DefaultRegularCloseTest(unittest.TestCase):
    """기본 종가는 KRX 정규장 종가다. J 는 애프터마켓까지 포함한 값이 내려오므로 TJ 로 맞춘다."""
    KEY = "market_candles_days"

    def test_omitted_market_and_basis_are_filled(self):
        out, notes = apply_defaults(self.KEY, {"iscd": "005930"})
        self.assertEqual(out["mrkt_div_code"], "TJ")
        self.assertEqual(out["exch_use_yn"], "Y")
        self.assertTrue(notes)

    def test_j_becomes_tj_when_basis_is_default(self):
        out, notes = apply_defaults(self.KEY, {"mrkt_div_code": "J", "iscd": "005930"})
        self.assertEqual((out["mrkt_div_code"], out["exch_use_yn"]), ("TJ", "Y"))
        self.assertTrue(any("'J'" in n for n in notes))

    def test_j_becomes_tj_when_basis_is_explicit_y(self):
        out, _ = apply_defaults(self.KEY, {"mrkt_div_code": "J", "exch_use_yn": "Y", "iscd": "005930"})
        self.assertEqual(out["mrkt_div_code"], "TJ")

    def test_explicit_n_keeps_the_request_as_it_is(self):
        # 애프터마켓 포함 값이 필요하다는 뜻이므로 바꾸지 않는다.
        params = {"mrkt_div_code": "J", "exch_use_yn": "N", "iscd": "005930"}
        out, notes = apply_defaults(self.KEY, params)
        self.assertEqual(out, params)
        self.assertEqual(notes, [])

    def test_tj_and_nj_are_left_alone(self):
        for m in ("TJ", "NJ"):
            out, notes = apply_defaults(self.KEY, {"mrkt_div_code": m, "exch_use_yn": "Y", "iscd": "005930"})
            self.assertEqual(out["mrkt_div_code"], m)
            self.assertEqual(notes, [])

    def test_other_params_are_kept(self):
        out, _ = apply_defaults(self.KEY, {"iscd": "005930", "from": "20260929", "to": "20261006"})
        self.assertEqual((out["iscd"], out["from"], out["to"]), ("005930", "20260929", "20261006"))

    def test_other_apis_are_untouched(self):
        params = {"mrkt_div_code": "J", "iscd": "005930"}
        out, notes = apply_defaults("market_prices", params)
        self.assertEqual(out, params)
        self.assertEqual(notes, [])

    def test_input_is_not_mutated(self):
        params = {"mrkt_div_code": "J", "iscd": "005930"}
        apply_defaults(self.KEY, params)
        self.assertEqual(params, {"mrkt_div_code": "J", "iscd": "005930"})

    def test_minimal_request_passes_validation_after_defaults(self):
        # mrkt_div_code 는 카탈로그에서 필수다. 기본값을 먼저 채워야 INVALID_PARAMS 가 안 난다.
        api = CAT.get(self.KEY)
        self.assertTrue(validate(api, {"iscd": "005930"}))
        out, _ = apply_defaults(self.KEY, {"iscd": "005930"})
        self.assertEqual(validate(api, out), [])

    def test_defaults_target_exists_in_catalog(self):
        for key, d in API_DEFAULTS.items():
            api = CAT.get(key)
            names = {p["name"] for p in api.params}
            self.assertTrue(set(d) <= names, key)


class SecurityTest(unittest.TestCase):
    def test_describe_has_no_credentials(self):
        for a in CAT.rest():
            text = describe(a, CAT)
            self.assertNotIn("Bearer ey", text, a.key)

    def test_error_dict_has_no_secret(self):
        e = MeritzError("실패", status=401, body={"msg": "no"}, code="AUTH_FAILED")
        self.assertNotIn("secret", json.dumps(e.as_dict(), ensure_ascii=False).lower())

    def test_mac_address_is_hex(self):
        self.assertRegex(mac_address(), r"^[0-9A-F]{12}$")

    def test_no_dev_server_trace_anywhere_in_the_repo(self):
        """공개 저장소와 배포물에는 운영 기준만 들어간다.

        파이썬 소스뿐 아니라 카탈로그·corrections·.env.example·manifest·문서까지
        **커밋된** 모든 텍스트 파일을 본다. 한 곳이라도 남으면 운영이 아닌 서버 주소가
        배포 실행 파일에 실려 나간다. 검사 대상은 git 이 추적하는 파일이다 —
        로컬 .env 나 빌드 venv 는 커밋되지 않으므로 보지 않는다.
        """
        needle = "dev" + "api"      # 이 파일 자신이 걸리지 않도록 쪼갠다
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                             capture_output=True)
        if out.returncode != 0:
            self.skipTest("git 저장소가 아니어서 커밋된 파일 목록을 얻을 수 없다")
        me = Path(__file__).resolve()
        hits = []
        for rel in out.stdout.decode().split("\0"):
            if not rel:
                continue
            path = ROOT / rel
            if not path.is_file() or path.resolve() == me:
                continue
            try:
                src = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue                        # 바이너리·읽을 수 없는 파일은 건너뛴다
            if needle in src:
                hits.append(rel)
        self.assertEqual(hits, [], f"운영이 아닌 서버 주소가 남아 있습니다: {hits}")


class ToolMappingTest(unittest.TestCase):
    def test_every_rest_category_has_a_tool(self):
        """도구가 없는 카테고리는 NO_TOOL 에 이유와 함께 적혀 있어야 한다."""
        from meritz.tools import NO_TOOL
        cats = {a["category"] for a in CAT.rest()}
        missing = cats - set(TOOL_OF) - set(NO_TOOL)
        self.assertEqual(missing, set(), f"도구가 없는 카테고리: {missing}")
        for c in cats & set(NO_TOOL):
            self.assertTrue(NO_TOOL[c].strip(), f"{c}: 제외 이유가 비어 있다")

    def test_realtime_is_not_served_here(self):
        self.assertNotIn("realtime", TOOL_OF)




# ---------------------------------------------------------------------------
# 주문·판정 회귀 방지. 이 분기가 깨지면 실패가 성공으로 보고된다.
# ---------------------------------------------------------------------------
from meritz.client import _is_empty  # noqa: E402
from meritz.safety import (  # noqa: E402
    check_confirm,
    issue_confirm_token,
)

DEGRADED = {"market_prices", "market_prices_after", "market_orderbook",
            "market_orderbook_after", "market_trades_ticks", "market_trades_minutes",
            "market_candles_minutes", "market_candles_days"}


class RegressionJudgeTest(unittest.TestCase):
    """오류를 성공으로 보고하면 안 된다."""

    def test_readable_error_code_is_not_treated_as_corrupted(self):
        """'8005' 는 읽히는 코드다. data 가 있어도 업무 오류다."""
        r = ApiClient.judge(CAT.get("market_prices"), 200,
                            {"rsp_cd": "8005", "rsp_msg": "초과", "data": {"x": 1}},
                            "u", DEGRADED)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "BUSINESS_ERROR")

    def test_rejected_order_is_not_success(self):
        r = ApiClient.judge(CAT.get("orders_buy"), 200,
                            {"rsp_cd": "8005", "rsp_msg": "주문가능수량 초과", "data": {}},
                            "u", DEGRADED)
        self.assertFalse(r["ok"])

    def test_corrupted_with_empty_shell_is_failure(self):
        """없는 종목을 조회하면 값이 전부 0인 껍데기가 온다 — 성공이 아니다."""
        r = ApiClient.judge(CAT.get("market_prices"), 200,
                            {"rsp_cd": "\x00\x118l",
                             "data": {"stck_prpr": 0, "marg_rate": "0", "kor_isnm": ""}},
                            "u", DEGRADED)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "EMPTY_RESULT")

    def test_corrupted_outside_whitelist_is_failure(self):
        """알려진 손상 목록에 없으면 성공으로 넘기지 않는다."""
        r = ApiClient.judge(CAT.get("valuation"), 200,
                            {"rsp_cd": "\x00\x11", "data": {"tfam": 1}}, "u", DEGRADED)
        self.assertFalse(r["ok"])

    def test_state_change_with_corrupted_code_is_never_success(self):
        r = ApiClient.judge(CAT.get("orders_buy"), 200,
                            {"rsp_cd": "\x00\x11", "data": {"oder_no": 1}},
                            "u", DEGRADED | {"orders_buy"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "UNVERIFIABLE_STATE_CHANGE")

    def test_corrupted_with_real_data_passes_unverified(self):
        r = ApiClient.judge(CAT.get("market_prices"), 200,
                            {"rsp_cd": "\x00\x11", "data": {"stck_prpr": 417500}},
                            "u", DEGRADED)
        self.assertTrue(r["ok"])
        self.assertFalse(r["verified"])

    def test_empty_helper(self):
        self.assertTrue(_is_empty(None))
        self.assertTrue(_is_empty([]))
        self.assertTrue(_is_empty({"a": 0, "b": "", "c": "0"}))
        self.assertFalse(_is_empty({"a": 0, "b": 1}))
        self.assertFalse(_is_empty([{"x": 1}]))


class RegressionGateTest(unittest.TestCase):
    """게이트를 우회할 수 없어야 한다."""

    def test_wrong_method_still_gated_by_path(self):
        """메서드가 잘못 등록돼도 경로가 막아야 한다."""
        fake = {"key": "orders_buy", "name": "x", "path": "/trading/v1/orders/buy",
                "method": "GET", "tr_id": "t", "protocol": "REST"}
        self.assertTrue(is_state_changing(fake))

    def test_all_action_paths_gated_regardless_of_method(self):
        for path in ("/trading/v1/orders/sell", "/trading/v1/orders/cancel",
                     "/trading/v1/credit-orders/buy",
                     "/trading/v1/overseas/orders/modify", "/forex/v1/exchanges"):
            self.assertTrue(
                is_state_changing({"path": path, "method": "GET", "protocol": "REST"}), path)

    def test_confirm_flag_alone_does_not_pass(self):
        """confirm=true 를 첫 호출에 같이 넣어도 통과하면 안 된다."""
        self.assertIsNotNone(check_confirm("orders_buy", {"a": 1}, None, PROD))

    def test_forged_token_rejected(self):
        self.assertIsNotNone(check_confirm("orders_buy", {"a": 1}, "deadbeefdeadbeef", PROD))

    def test_token_is_bound_to_body(self):
        t = issue_confirm_token("orders_buy", {"odqt": 1}, PROD)
        self.assertIsNotNone(check_confirm("orders_buy", {"odqt": 99}, t, PROD))

    def test_token_is_bound_to_api(self):
        t = issue_confirm_token("orders_buy", {"odqt": 1}, PROD)
        self.assertIsNotNone(check_confirm("orders_sell", {"odqt": 1}, t, PROD))

    def test_token_is_single_use(self):
        t = issue_confirm_token("orders_buy", {"odqt": 1}, PROD)
        self.assertIsNone(check_confirm("orders_buy", {"odqt": 1}, t, PROD))
        self.assertIsNotNone(check_confirm("orders_buy", {"odqt": 1}, t, PROD))

    def test_string_is_rejected(self):
        with self.assertRaises(TypeError):
            is_state_changing("orders_buy")


class RegressionConfigTest(unittest.TestCase):
    """설정 안전장치."""

    def setUp(self):
        self.saved = {k: os.environ.get(k)
                      for k in ("MERITZ_BASE_URL", "MERITZ_WS_URL",
                                "MERITZ_NO_ENV_FILE", "MERITZ_APP_SECRET")}
        for k in self.saved:
            os.environ.pop(k, None)
        os.environ["MERITZ_NO_ENV_FILE"] = "1"

    def tearDown(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_secret_not_in_repr(self):
        os.environ["MERITZ_APP_SECRET"] = "SUPERSECRET-XYZ"
        self.assertNotIn("SUPERSECRET", repr(settings()))

    def test_other_host_does_not_use_prod_websocket(self):
        os.environ["MERITZ_BASE_URL"] = OTHER
        self.assertNotIn("openapi", settings().ws_url)

    def test_unknown_host_does_not_guess_websocket(self):
        os.environ["MERITZ_BASE_URL"] = "https://other.example.com"
        self.assertEqual(settings().ws_url, "")

    def test_prod_without_port_still_uses_prod_websocket(self):
        os.environ["MERITZ_BASE_URL"] = "https://openapi.imeritz.com"
        self.assertEqual(settings().ws_url, PROD_WS)

    def test_http_is_rejected(self):
        os.environ["MERITZ_BASE_URL"] = "http://example.invalid:9443"
        with self.assertRaises(ValueError):
            settings()


class RegressionCatalogTest(unittest.TestCase):
    """카탈로그 자체의 건전성."""

    def test_duplicate_key_raises(self):
        from meritz.catalog import Catalog
        dup = {"apis": [{"key": "x", "protocol": "REST"}, {"key": "x", "protocol": "REST"}]}
        with self.assertRaises(ValueError):
            Catalog(dup)

    def test_websocket_endpoint_has_path(self):
        for a in CAT.websockets():
            self.assertTrue(a["domain"]["prod"].endswith("/websocket"), a.key)

    def test_response_has_no_wrapper_fields(self):
        for a in CAT.rest():
            top = [p["name"] for p in a.get("response", {}).get("body", [])
                   if p.get("depth", 0) == 0]
            for w in ("data", "header", "body"):
                self.assertNotIn(w, top, f"{a.key} 응답에 래퍼 {w} 가 필드로 남아 있다")


def test_degraded_keys_default_to_catalog():
    """호출자가 안 넘겨도 손상 API 를 알아야 한다.

    안 넘기면 정상 응답이 UNREADABLE_RESPONSE_CODE 로 튕긴다.
    """
    from meritz.catalog import load_catalog
    from meritz.client import ApiClient
    from meritz.config import Settings
    c = ApiClient(Settings())
    expected = {k for x in (load_catalog().corrections.get("corrections") or [])
                if x.get("degraded") for k in x.get("keys", [])}
    assert c.degraded_keys == expected
    assert expected, "손상 API 목록이 비면 이 방어는 의미가 없다"


def test_explicit_empty_degraded_keys_is_respected():
    """빈 집합을 명시로 넘기면 그대로 둔다 — 아무것도 봐주지 않는 쪽이다."""
    from meritz.client import ApiClient
    from meritz.config import Settings
    assert ApiClient(Settings(), degraded_keys=set()).degraded_keys == set()


class ConfirmTokenBindingTest(unittest.TestCase):
    """확인 토큰이 '무엇을·어디로' 양쪽에 묶여 있는지."""

    def test_token_from_another_server_is_rejected_on_prod(self):
        """미리보기 서버와 전송 서버가 다르면 거절해야 한다.

        기본값이 운영이라, 두 번째 명령에서 환경변수를 빠뜨리면 사용자가 본
        화면과 다른 서버로 주문이 나갈 수 있다.
        """
        body = {"odqt": 1, "iscd": "A005930"}
        t = issue_confirm_token("orders_buy", body, OTHER)
        self.assertIsNotNone(check_confirm("orders_buy", body, t, PROD))

    def test_token_works_on_the_server_it_was_issued_for(self):
        body = {"odqt": 1}
        t = issue_confirm_token("orders_buy", body, OTHER)
        self.assertIsNone(check_confirm("orders_buy", body, t, OTHER))

    def test_token_is_not_derivable_offline(self):
        """예측 가능한 값이면 미리보기를 거치지 않고도 만들 수 있다.

        그러면 '토큰이 있다 = 사용자가 내용을 봤다' 가 성립하지 않는다.
        """
        from meritz.safety import _fingerprint
        body = {"odqt": 9999, "oder_unpr": 1}
        t = issue_confirm_token("orders_buy", body, PROD)
        self.assertNotEqual(t, _fingerprint("orders_buy", body, PROD))

    def test_same_order_gets_a_different_token_each_time(self):
        body = {"odqt": 1}
        a = issue_confirm_token("orders_buy", body, PROD)
        b = issue_confirm_token("orders_buy", body, PROD)
        self.assertNotEqual(a, b)

    def test_preview_binds_the_token_to_the_shown_url(self):
        api = CAT.get("orders_buy")
        body = {"odqt": 1}
        pv = preview(api, body, OTHER)
        self.assertIn(OTHER, pv["will_send"]["url"])
        self.assertIsNotNone(
            check_confirm(api.key, body, pv["confirm_token"], PROD))


class OrderNotAcceptedTest(unittest.TestCase):
    """접수되지 않은 주문을 성공으로 보고하지 않는지.

    rsp_cd 는 "0001" 이고 메시지는 "주문이 완료되었습니다" 인데,
    warn_cls_code 가 경고면 시장에는 아무것도 나가 있지 않다.
    표시 계층이 아니라 판정에서 잡아야 MCP·CLI·라이브러리 전부에 걸린다.
    """

    API = None

    @classmethod
    def setUpClass(cls):
        cls.API = CAT.get("orders_buy")

    def _judge(self, body):
        return ApiClient.judge(self.API, 200, body, "https://x/trading/v1/orders/buy")

    def test_warning_in_single_object_is_caught(self):
        r = self._judge({"rsp_cd": "0001", "rsp_msg": "주문이 완료되었습니다.",
                         "data": {"oder_no": 140, "warn_cls_code": "c",
                                  "warn_msg": "위탁증거금 부족 종목입니다."}})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "ORDER_NOT_ACCEPTED")
        self.assertIn("위탁증거금", r["message"])
        self.assertNotIn("note", r)

    def test_warning_in_list_is_caught(self):
        r = self._judge({"rsp_cd": "0001",
                         "data": [{"warn_cls_code": "0"}, {"warn_cls_code": "g"}]})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "ORDER_NOT_ACCEPTED")

    def test_warning_at_top_level_is_caught(self):
        r = self._judge({"rsp_cd": "0001", "warn_cls_code": "1", "warn_msg": "확인 필요"})
        self.assertFalse(r["ok"])

    def test_zero_is_not_a_warning(self):
        r = self._judge({"rsp_cd": "0001", "data": {"oder_no": 140,
                                                    "warn_cls_code": "0", "warn_msg": ""}})
        self.assertTrue(r["ok"])
        self.assertIn("접수 성공일 뿐", r["note"])

    def test_every_documented_warning_code_is_caught(self):
        for code in ("1", "3", "4", "6", "7", "9", "a", "c", "d", "f", "g"):
            with self.subTest(code=code):
                r = self._judge({"rsp_cd": "0001", "data": {"warn_cls_code": code}})
                self.assertFalse(r["ok"], f"{code} 를 성공으로 보고합니다")


class EmptyResultTest(unittest.TestCase):
    """없는 종목을 "0원" 으로 성공 보고하지 않는지."""

    def _judge(self, data):
        api = CAT.get("market_prices")
        return ApiClient.judge(api, 200, {"rsp_cd": "\x00\x01", "data": data},
                               "https://x", degraded_keys={"market_prices"})

    def test_unknown_symbol_is_not_a_success(self):
        """게이트웨이가 상수 필드를 채워 보내므로 '값이 있으면 성공' 은 안 된다.

        게이트웨이가 시장명 같은 상수 필드를 채워 보내므로
        '값이 있으면 성공' 으로 볼 수 없다.
        """
        r = self._judge({"shrn_iscd": "", "kor_isnm": "", "stck_prpr": 0,
                         "acml_vol": 0, "rprs_mrkt_kor_name": "KOSPI",
                         "tmpr_susp_yn": "N", "crdt_able_yn": "Y"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "EMPTY_RESULT")

    def test_real_symbol_still_passes(self):
        r = self._judge({"shrn_iscd": "005930", "kor_isnm": "삼성전자",
                         "stck_prpr": 496500, "acml_vol": 12345})
        self.assertTrue(r["ok"])
        self.assertFalse(r["verified"])

    def test_zero_price_with_a_real_symbol_is_not_treated_as_empty(self):
        """거래정지 종목은 현재가가 0 일 수 있다. 종목이 확인되면 결과가 있는 것이다."""
        r = self._judge({"shrn_iscd": "005930", "kor_isnm": "삼성전자",
                         "stck_prpr": 0, "acml_vol": 0})
        self.assertTrue(r["ok"])

    def test_response_without_echo_fields_falls_back(self):
        self.assertTrue(_is_empty({"a": "", "b": 0}))
        self.assertFalse(_is_empty({"a": "값"}))


class NoConfirmKillSwitchTest(unittest.TestCase):
    """확인 게이트를 끄는 스위치가 없어야 한다.

    끄는 방법이 있으면 언젠가 그 상태로 돌아가고, 그때 주문이 무확인으로 나간다.
    """

    def test_no_env_var_disables_the_gate(self):
        import meritz.config as cfg
        import meritz.safety as saf
        import meritz.tools as tools
        src = "".join(open(m.__file__, encoding="utf-8").read()
                      for m in (cfg, saf, tools))
        self.assertNotIn("MERITZ_ORDER_CONFIRM", src)
        self.assertNotIn("order_confirm", src)

    def test_settings_has_no_order_confirm_field(self):
        from meritz.config import Settings
        self.assertFalse(hasattr(Settings(), "order_confirm"))


if __name__ == "__main__":
    unittest.main()


class JosaTest(unittest.TestCase):
    """API 이름이 문장에 들어가므로 조사를 고정할 수 없다."""

    def test_picks_by_final_consonant(self):
        from meritz.safety import josa
        self.assertEqual(josa("일반주문 매수"), "는")     # 받침 없음
        self.assertEqual(josa("환전신청"), "은")          # 받침 있음
        self.assertEqual(josa("주문", "이가"), "이")
        self.assertEqual(josa("매수", "이가"), "가")

    def test_non_hangul_falls_back(self):
        from meritz.safety import josa
        self.assertEqual(josa("orders_buy"), "는")
        self.assertEqual(josa(""), "는")

    def test_blocked_message_uses_it(self):
        msg = blocked(CAT.get("orders_buy"))["message"]
        self.assertNotIn("' 은 ", msg)


class WebsocketResponseShapeTest(unittest.TestCase):
    """실시간 응답이 header 와 body 로 갈라져 있는지.

    포털은 res_b 안에서 header · body 래퍼로 필드를 중첩한다. 래퍼를 무시하고
    전부 body 로 몰면 tr_cd·tr_key 가 body 필드로 등록돼, 실제로는 header 로
    오므로 "선언했는데 안 오는 필드" 가 된다.
    """

    def test_subscription_keys_are_declared_in_header(self):
        for a in CAT.websockets():
            with self.subTest(api=a.key):
                head = {f["name"] for f in a.get("response", {}).get("header", [])}
                body = {f["name"] for f in a.get("response", {}).get("body", [])}
                self.assertEqual({"tr_cd", "tr_key"} & head, {"tr_cd", "tr_key"},
                                 f"{a.key}: tr_cd·tr_key 가 header 에 없다")
                self.assertFalse({"tr_cd", "tr_key"} & body,
                                 f"{a.key}: tr_cd·tr_key 가 body 에 남아 있다")

    def test_body_has_real_fields(self):
        for a in CAT.websockets():
            with self.subTest(api=a.key):
                body = a.get("response", {}).get("body", [])
                self.assertGreater(len(body), 5, f"{a.key}: 응답 필드가 비어 있다")


class OverseasWarnSchemeTest(unittest.TestCase):
    """국내와 해외는 warn_cls_code 체계가 다르다.

    명세가 "국내주식 주문과 값 체계가 다르므로 코드를 공용으로 쓰지 마십시오" 라고
    적어 두었다. 국내 목록으로 해외를 판정하면 해외 "2"(보류) 가 성공으로 나간다.
    """

    def _judge(self, key, code):
        api = CAT.get(key)
        return ApiClient.judge(api, 200,
                               {"rsp_cd": "0001", "data": {"warn_cls_code": code}},
                               "https://x" + api["path"])

    def test_overseas_hold_code_is_not_success(self):
        r = self._judge("ovs_orders_buy", "2")
        self.assertFalse(r["ok"], "해외 보류(2) 를 성공으로 보고합니다")
        self.assertFalse(r["resendable"])
        self.assertIn("다시 보내도 접수되지 않습니다", r["message"])

    def test_overseas_warning_is_resendable(self):
        r = self._judge("ovs_orders_buy", "1")
        self.assertFalse(r["ok"])
        self.assertTrue(r["resendable"])

    def test_domestic_hold_codes_are_caught(self):
        """국내 보류 2·5·8·b·e·h 도 미접수다. 경고 목록만 보면 놓친다."""
        for code in "258beh":
            with self.subTest(code=code):
                r = self._judge("orders_buy", code)
                self.assertFalse(r["ok"], f"국내 보류({code}) 를 성공으로 보고합니다")
                self.assertFalse(r["resendable"])

    def test_domestic_warning_codes_are_resendable(self):
        for code in "134679acdfg":
            with self.subTest(code=code):
                r = self._judge("orders_buy", code)
                self.assertFalse(r["ok"])
                self.assertTrue(r["resendable"])

    def test_zero_is_accepted_everywhere(self):
        for key in ("orders_buy", "ovs_orders_buy"):
            with self.subTest(api=key):
                self.assertTrue(self._judge(key, "0")["ok"])

    def test_unknown_code_is_still_not_accepted(self):
        """목록에 없는 값이 와도 0 이 아니면 접수된 것이 아니다."""
        r = self._judge("orders_buy", "z")
        self.assertFalse(r["ok"])


class ReadOnlyDefaultTest(unittest.TestCase):
    """주문은 열어야 열리는 것이어야 한다.

    기본이 열려 있으면, 조회만 하려던 사람이 설정 한 줄을 빠뜨렸을 때
    에이전트가 주문을 낼 수 있는 상태가 된다.
    """

    def test_server_entrypoint_turns_on_read_only(self):
        """MCP 서버는 조회 전용으로 시작해야 한다.

        기본이 열려 있으면, 조회만 하려던 사람이 설정 한 줄을 빠뜨렸을 때
        에이전트가 주문을 낼 수 있는 상태가 된다.
        """
        import importlib

        import meritz.config as cfg
        import meritz.server  # noqa: F401
        importlib.reload(cfg) if False else None
        self.assertEqual(cfg._READ_ONLY_DEFAULT, "1")

    def test_env_can_open_orders(self):
        import os

        import meritz.config as cfg
        os.environ["MERITZ_READ_ONLY"] = "0"
        try:
            self.assertFalse(cfg.settings().read_only)
        finally:
            os.environ.pop("MERITZ_READ_ONLY", None)

    def test_unreadable_switch_value_falls_back_to_safe(self):
        """안전 스위치가 오타 하나로 열리면 안 된다.

        빈 값·공백·알아볼 수 없는 값이 오면 기본값으로 돌아가야 한다.
        예전에는 이런 값이 전부 False(주문 허용)로 떨어졌다.
        """
        import os

        from meritz import config as cfg
        for bad in ("", "   ", "yes please", "참", "1 "):
            with self.subTest(value=bad):
                os.environ["MERITZ_READ_ONLY"] = bad
                try:
                    self.assertTrue(cfg.settings().read_only, f"{bad!r} 에서 열렸다")
                finally:
                    os.environ.pop("MERITZ_READ_ONLY", None)

    def test_orders_are_blocked_when_read_only(self):
        from meritz.client import ApiClient, MeritzError
        from meritz.config import Settings
        c = ApiClient(Settings(app_key="k", app_secret="s", read_only=True))
        with self.assertRaises(MeritzError) as cm:
            c.call(CAT.get("orders_buy"), {})
        self.assertEqual(cm.exception.code, "READ_ONLY")


class ToolAnnotationTest(unittest.TestCase):
    """도구마다 사람이 읽는 이름과 힌트가 붙어야 한다."""

    def test_every_tool_has_title_and_hints(self):
        import meritz.tools as t
        seen = {}

        class FakeMCP:
            def tool(self, **kw):
                def deco(fn):
                    seen[kw["name"]] = kw
                    return fn
                return deco

        from meritz.client import ApiClient
        from meritz.config import Settings
        s = Settings()
        t.register(FakeMCP(), CAT, Registry([s]), s)
        self.assertTrue(seen, "도구가 하나도 등록되지 않았다")
        for name, kw in seen.items():
            with self.subTest(tool=name):
                a = kw.get("annotations") or {}
                self.assertTrue(a.get("title"), f"{name}: title 없음")
                self.assertIn("readOnlyHint", a)
                self.assertIn("destructiveHint", a)
                self.assertTrue(kw.get("description"))


class ToolErrorTest(unittest.TestCase):
    """실패는 MCP 오류로 올라가야 한다.

    ``{"ok": false}`` 를 정상 응답으로 돌려주면 프로토콜 수준에서는 성공이라,
    호스트가 실패를 세거나 재시도를 판단할 근거가 없다.
    """

    def _tools(self, **kw):
        import meritz.tools as t
        from meritz.client import ApiClient
        from meritz.config import Settings
        fns = {}

        class FakeMCP:
            def tool(self, **k):
                def deco(fn):
                    fns[k["name"]] = fn
                    return fn
                return deco

        s = Settings(app_key="k", app_secret="s", **kw)
        t.register(FakeMCP(), CAT, Registry([s]), s)
        return fns

    def _run(self, fn, api_type, **params):
        import asyncio
        return asyncio.run(fn(api_type, params))

    def test_unknown_api_type_is_an_error(self):
        from fastmcp.exceptions import ToolError
        fn = next(iter(self._tools().values()))
        with self.assertRaises(ToolError) as cm:
            self._run(fn, "없는_이름")
        self.assertIn("UNKNOWN_API_TYPE", str(cm.exception))

    def test_read_only_block_is_an_error(self):
        from fastmcp.exceptions import ToolError
        fns = self._tools(read_only=True)
        name, fn = next((n, f) for n, f in fns.items() if n == "meritz_trading")
        with self.assertRaises(ToolError):
            self._run(fn, "orders_buy")

    def test_preview_is_not_an_error(self):
        """확인 대기는 실패가 아니다. 오류로 올리면 흐름이 끊긴다."""
        # 기본값이 조회 전용이라, 주문 경로를 보려면 여기서 명시적으로 연다.
        fns = self._tools(read_only=False)
        fn = next(f for n, f in fns.items() if n == "meritz_trading")
        out = self._run(fn, "orders_buy", iscd="A005930", odqt=1,
                        oder_unpr=50000, oder_cls_code="01",
                        oder_cond_cls_code="0", orgl_oder_no=0,
                        whol_rctf_cncl_yn="N", warn_cnfr_yn="N",
                        exch_kind_code="01")
        self.assertIsInstance(out, dict)
        self.assertIn("confirm_token", json.dumps(out, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 문서와 실제 도구 목록이 어긋나지 않게 한다.
#
# 도구를 하나 더 낸 적이 있는데(해외 시세 분리) README·llms.txt·manifest.json 은
# 다섯 개 그대로였다. 이용자는 문서를 보고 도구를 고르니, 문서가 틀리면
# 없는 도구를 부르거나 있는 도구를 못 본다. 서버를 실제로 띄워 tools/list 를
# 받아 대조한다 — 소스의 TOOL_OF 만 보면 등록 단계의 실수를 잡지 못한다.
# ---------------------------------------------------------------------------
class StrategyExamplesTest(unittest.TestCase):
    def test_bundled_examples_follow_contract_and_produce_distinct_behavior(self):
        """Execute only reviewed repository fixtures, not caller-supplied strategy text."""
        import runpy

        from meritz.backtest_engine import demo_candles
        from meritz.strategy_engine import DEFAULTS, run_strategy
        from meritz.strategy_runner import validate_code

        reports = {}
        for name in ("hold", "buy_once", "mean_reversion"):
            with self.subTest(example=name):
                path = ROOT / "examples" / "strategies" / f"{name}.py"
                validation = validate_code(path.read_text(encoding="utf-8"))
                self.assertTrue(validation["valid"], validation["errors"])
                callback = runpy.run_path(str(path))["on_bar"]
                reports[name] = run_strategy(demo_candles(), DEFAULTS, callback, None,
                                             {"name": name, "codeHash": validation["codeHash"]})
        self.assertEqual(reports["hold"]["metrics"]["tradeCount"], 0)
        self.assertEqual(reports["buy_once"]["metrics"]["tradeCount"], 1)
        self.assertGreater(reports["mean_reversion"]["metrics"]["tradeCount"], 1)
        self.assertEqual(len({report["runId"] for report in reports.values()}), 3)


class ToolsMatchDocsTest(unittest.TestCase):
    @staticmethod
    def _live_tool_names() -> list[str]:
        """서버를 띄워 tools/list 로 받은 실제 도구 이름."""
        import asyncio

        from fastmcp import Client

        from meritz.server import build_server

        async def go():
            mcp, _ = build_server()
            async with Client(mcp) as c:
                return [t.name for t in await c.list_tools()]

        return asyncio.run(go())

    def setUp(self):
        self.live = set(self._live_tool_names())
        self.assertTrue(self.live, "tools/list 가 비어 있다")

    def test_live_list_matches_tool_of_plus_info(self):
        from meritz.backtest_tools import TOOL_NAMES
        self.assertEqual(self.live, set(TOOL_OF.values()) | {"meritz_info"} | set(TOOL_NAMES))

    def test_readme_lists_every_tool(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in self.live:
            with self.subTest(tool=name):
                self.assertIn(f"`{name}`", text, f"README 에 {name} 가 없다")

    def test_llms_txt_lists_every_tool(self):
        text = (ROOT / "llms.txt").read_text(encoding="utf-8")
        for name in self.live:
            with self.subTest(tool=name):
                self.assertIn(f"`{name}`", text, f"llms.txt 에 {name} 가 없다")

    def test_manifest_tools_match(self):
        m = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual({t["name"] for t in m["tools"]}, self.live)

    def test_issue_templates_list_every_tool(self):
        # 이슈 양식은 저장소 루트(open-api)의 .github 에 있다. Studio 는 strategy-studio/ 하위 폴더다.
        for f in ("strategy-studio-beta.yml",):
            text = (ROOT.parent / ".github" / "ISSUE_TEMPLATE" / f).read_text(encoding="utf-8")
            for name in self.live:
                with self.subTest(file=f, tool=name):
                    self.assertIn(f"- {name} —", text, f"{f} 드롭다운에 {name} 가 없다")

    def test_readme_api_counts_match_the_catalog(self):
        """도구 표의 건수는 카탈로그에서 센 값과 같아야 한다."""
        import re
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for category, tool in TOOL_OF.items():
            want = len([a for a in CAT.by_category(category) if not a.is_websocket])
            row = re.search(rf"^\|\s*`{tool}`\s*\|.*$", text, re.M)
            self.assertIsNotNone(row, f"README 도구 표에 {tool} 행이 없다")
            self.assertIn(f"{want}건", row.group(0),
                          f"{tool}: README 는 {row.group(0)!r}, 카탈로그는 {want}건")

    def test_docs_do_not_claim_all_rest_apis_are_callable(self):
        """도구로 부를 수 있는 것은 REST 전체가 아니다(oauth2 제외)."""
        from meritz.tools import NO_TOOL
        exposed = len([a for a in CAT.rest() if a["category"] in TOOL_OF])
        total = len(CAT.rest())
        self.assertLess(exposed, total)
        self.assertEqual(total - exposed,
                         len([a for a in CAT.rest() if a["category"] in NO_TOOL]))
        for f in ("README.md", "llms.txt"):
            text = (ROOT / f).read_text(encoding="utf-8")
            with self.subTest(file=f):
                # 문서는 줄바꿈이 어디에 들어갈지 모른다. 숫자만 본다.
                self.assertRegex(text, rf"REST\s+{total}건\s+중\s+{exposed}건")


class CodexConfigExampleTest(unittest.TestCase):
    """README 의 Codex 설정 예시는 TOML 로 읽혀야 한다.

    ``env_vars = { … }`` 인라인 테이블은 Codex 가 시퀀스를 기대해 거부하고,
    그 서버 하나가 아니라 ``~/.codex/config.toml`` 전체 로드가 실패한다.
    """

    def _toml_blocks(self) -> list[str]:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        out, buf, on = [], [], False
        for line in text.splitlines():
            if line.strip() == "```toml":
                on, buf = True, []
            elif on and line.strip() == "```":
                on = False
                out.append("\n".join(buf))
            elif on:
                buf.append(line)
        return out

    def test_codex_example_parses_and_uses_an_env_table(self):
        try:
            import tomllib
        except ModuleNotFoundError:                     # pragma: no cover
            self.skipTest("tomllib 없음")
        blocks = self._toml_blocks()
        if not blocks:
            # README 가 CLI 등록 명령만 안내하는 시기가 있다. toml 예시가
            # 돌아오면 이 검사도 함께 살아난다.
            self.skipTest("README 에 toml 예시가 없다")
        for block in blocks:
            with self.subTest(block=block[:40]):
                self.assertNotIn("env_vars", block,
                                 "env_vars 는 Codex 가 못 읽는다. [mcp_servers.<이름>.env] 를 쓴다")
                cfg = tomllib.loads(block)
                for server in cfg.get("mcp_servers", {}).values():
                    self.assertIn("command", server)
                    self.assertIsInstance(server.get("env", {}), dict)
                    self.assertIn("MERITZ_APP_KEY", server.get("env", {}))
