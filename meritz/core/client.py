"""REST 호출.

토큰은 만료 전까지 재사용한다. 응답은 HTTP 200 만으로 성공을 단정하지 않는다 —
국내 시세 8건은 정상 데이터를 주면서 rsp_cd 에 손상된 값이 온다.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
import warnings
from typing import Any

import requests

from .config import PROD
from .safety import check_confirm, is_state_changing, josa, preview

# 성공으로 보는 코드. 5820·5822 는 "자료 없음" 이라 오류가 아니다 —
# 빈 기간을 조회하면 5822 가 오는데, 이걸 실패로 보고하면
# "자료 없음" 이 "조회 실패" 로 둔갑한다.
RETRYABLE = {"EGW00200"}          # 초당 거래건수 초과
# 토큰이 무효가 됐다는 신호. 캐시를 버리고 한 번 다시 받아야 한다.
TOKEN_DEAD = {"EGW00121", "EGW00123"}
OK_CODES = {"0000", "0001", "5762", "5766", "5820", "5822"}
# "자료 없음" 을 뜻하는 코드. **코드만으로 자료 없음을 단정하지 않는다** —
# 게이트웨이가 자료를 담고도 5820 을 보내는 API 가 여럿이다(담보 현황·
# 국내 실현손익(종목별)·해외 실현손익). 판정은 아래 _payload_is_empty() 가 한다.
NO_DATA_CODES = {"5820", "5822"}

# 실행부에서 소비하는 키 — 명세 파라미터가 아니므로 검증 대상이 아니다
_META_KEYS = frozenset({"api_type", "confirm", "confirm_token", "tr_cont", "tr_cont_key"})


log = logging.getLogger("meritz")


class MeritzError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: Any = None,
                 code: str | None = None):
        super().__init__(message)
        self.status, self.body, self.code = status, body, code

    def as_dict(self) -> dict:
        return {"ok": False, "error": self.code or "CALL_FAILED",
                "message": str(self), "http_status": self.status, "body": self.body}


# 조회가 맞았는지를 알려 주는 되돌림 필드. 없는 종목을 물으면 여기가 빈다.
_ECHO_FIELDS = ("shrn_iscd", "iscd", "fidxr_iscd", "kor_isnm", "isnm",
                "stck_prpr", "prpr")

_BLANK: tuple[Any, ...] = (None, "", 0, "0", [], {})


def _is_empty(data: Any) -> bool:
    """빈 껍데기 판정.

    손상 응답 API 들은 없는 종목을 조회해도 오류 대신 값이 전부 0/빈 문자열인
    객체를 돌려준다. 그걸 성공으로 보고하면 "현재가 0원" 이 사실처럼 나간다.

    **"아무 값이나 있으면 채워진 것"으로 보면 안 된다.** 게이트웨이는 조회가
    빗나가도 rprs_mrkt_kor_name="KOSPI" · tmpr_susp_yn="N" 같은 상수 필드를
    함께 채워 보낸다. 그래서 종목코드·종목명·현재가처럼 **조회 결과가 되돌아오는
    필드**를 본다. 그것들이 응답에 있는데 전부 비어 있으면 찾은 것이 없는 것이다.
    """
    if data is None:
        return True
    if isinstance(data, (list, tuple)):
        return len(data) == 0
    if not isinstance(data, dict):
        return False

    echoed = [data[f] for f in _ECHO_FIELDS if f in data]
    if echoed:
        return all(v in _BLANK for v in echoed)
    return not any(v not in _BLANK for v in data.values())


def _payload_is_empty(body: Any) -> bool:
    """자료가 정말 없는가. rsp_cd 가 아니라 data 로만 판정한다.

    게이트웨이는 **자료를 담고도 5820("해당 자료가 없습니다")을 보낸다.**
    담보 현황은 총자산을 담고 5820, 국내 실현손익(종목별)은 자료를 담고
    5820, 해외 실현손익이 2건 + 합계를 담고 5820. 코드만 보고 no_data 를 달면
    고객은 있는 자료를 "자료 없음" 으로 읽는다.

    비었다는 판정에는 `[]` · `{}` · `None` 이 모두 들어간다. 이 API 집합은
    목록형이 빈 결과를 `[]` 로, 단건형이 객체를 유지한 채 내려준다.
    여기서는 _is_empty() 처럼 '값이 전부 0/빈 문자열' 까지 보지 않는다 —
    그건 rsp_cd 가 손상된 API 를 위한 더 센 판정이고, 여기에 쓰면
    합계가 0원인 정상 응답이 "자료 없음" 으로 둔갑한다.
    """
    if not isinstance(body, dict):
        return False
    data = body.get("data")
    if data is None:
        return True
    if isinstance(data, (list, tuple, dict, str)):
        return len(data) == 0
    return False


def _message(body: Any, fallback: str = "") -> str:
    """고객에게 보일 메시지를 만든다. rsp_msg 와 rsp_sub_msg 를 **함께** 쓴다.

    실제 조치 정보는 rsp_sub_msg 에 담긴다. rsp_msg 만 전달하면 고객이 받는 건
    무의미한 쪽뿐이다.

        rsp_cd 0760  rsp_msg "권한이 없습니다."       rsp_sub_msg "계좌 관리점과 직원소속부서 확인!!"
        rsp_cd 7936  rsp_msg "해당데이터가 없습니다."  rsp_sub_msg "조회시작일"

    앞쪽만 보이면 "왜 안 되는지" 를 알 수 없다. 두 쪽을 이어 붙인다.
    """
    if not isinstance(body, dict):
        return fallback
    main = str(body.get("rsp_msg") or "").strip()
    sub = str(body.get("rsp_sub_msg") or "").strip()
    if sub and sub != main:
        return f"{main} — {sub}" if main else sub
    return main or fallback


def _attach_sub_msg(out: dict, body: Any) -> None:
    """rsp_sub_msg 를 결과에 따로 남긴다.

    message 에 이미 합쳐 넣지만, 프로그램이 골라 쓸 수 있게 원문도 남긴다.
    (예: 8337 의 호가 단위, 7936 의 빠진 항목명)
    """
    if isinstance(body, dict):
        sub = str(body.get("rsp_sub_msg") or "").strip()
        if sub:
            out["rsp_sub_msg"] = sub


# 주문이 접수되지 않았는데 성공처럼 보이는 유일한 경로다.
#
# 명세의 요약 문장이 기준이다 — **"0" 이 아닌 값은 접수되지 않은 상태**.
# 국내와 해외는 코드 체계가 다르고, 명세가 "코드를 공용으로 쓰지 마십시오" 라고
# 명시한다. 그래서 값 목록으로 판정하지 않고 "0 인가" 로 판정한 뒤,
# 재전송으로 풀리는지만 체계별로 나눈다.
_RESEND_OK = {
    "domestic": frozenset("134679acdfg"),   # 경고 — warn_cnfr_yn="Y" 로 재전송하면 접수
    "overseas": frozenset("13"),            # 1 경고 · 3 미국 PTP 과세 확인
}


def _warn_scheme(api: Any) -> str:
    return "overseas" if "/overseas/" in (api.get("path") or "") else "domestic"


def _warning_row(body: Any, scheme: str = "domestic") -> tuple[str, str, bool] | None:
    """접수되지 않은 주문이면 (코드, 사유, 재전송으로 풀리는가). 아니면 None.

    응답 형태가 단건 dict 일 수도 배열일 수도 있고, 최상위에 올 수도 있다.
    한 형태만 보면 나머지에서 조용히 새어 나간다.
    """
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    rows = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
    for row in list(rows) + [body]:
        if not isinstance(row, dict) or "warn_cls_code" not in row:
            continue
        code = str(row.get("warn_cls_code", "")).strip()
        if code and code != "0":
            return code, str(row.get("warn_msg") or "").strip(), code in _RESEND_OK[scheme]
    return None


# 형식 검증에 쓰는 값들 — 카탈로그가 이미 들고 있는 length·설명을 그대로 쓴다.
_DATE8 = re.compile(r"^\d{8}$")


def _is_date_param(p: dict) -> bool:
    """날짜 파라미터인가. 명세가 길이 8 과 "YYYYMMDD" 설명으로 알려 준다.

    이름으로 찾지 않는다 — from·to·base_dt·oder_sta_date 처럼 이름 규칙이 없다.
    """
    return (str(p.get("length") or "") == "8"
            and "YYYYMMDD" in (p.get("description") or ""))


def _declared_length(p: dict) -> int:
    raw = str(p.get("length") or "").strip()
    return int(raw) if raw.isdigit() else 0


def mac_address() -> str:
    """게이트웨이 필수 헤더. 없으면 IGW50024 로 거절된다."""
    n = uuid.getnode()
    return "".join(f"{(n >> s) & 0xFF:02X}" for s in range(40, -1, -8))


class TokenManager:
    def __init__(self, settings):
        self.s = settings
        self._token: str | None = None
        self._expires = 0.0
        self._issued_for: tuple | None = None
        self._lock = threading.Lock()

    @property
    def _cache_key(self) -> tuple:
        """도메인이나 자격증명이 바뀌면 옛 토큰을 쓰지 않는다."""
        return (self.s.base_url, self.s.app_key)

    def invalidate(self) -> None:
        """캐시된 토큰을 버린다. 포털에서 폐기됐거나 앱키가 갱신된 경우."""
        with self._lock:
            self._token = None
            self._expires = 0.0
            self._issued_for = None

    def get(self) -> str:
        with self._lock:
            if (self._token and time.time() < self._expires
                    and self._issued_for == self._cache_key):
                return self._token
        if not self.s.has_credentials:
            raise MeritzError(
                "MERITZ_APP_KEY / MERITZ_APP_SECRET 환경변수가 필요합니다. "
                "메리츠 Open API 포털에서 앱키를 발급받아 설정하세요.",
                code="NO_CREDENTIALS")

        # HTTP 는 락 밖에서 한다. 토큰 서버가 느릴 때 전 스레드가 멈추지 않도록.
        try:
            r = requests.post(
                f"{self.s.base_url}/oauth2/token",
                data={"grant_type": "client_credentials",
                      "client_id": self.s.app_key, "client_secret": self.s.app_secret,
                      "scope": "login"},
                headers={"content-type": "application/x-www-form-urlencoded"},
                timeout=self.s.timeout)
        except requests.RequestException as e:
            raise MeritzError(f"토큰 서버에 연결하지 못했습니다: {e}",
                              code="AUTH_UNREACHABLE") from None
        if r.status_code != 200:
            # 게이트웨이는 무엇이 틀렸는지를 알려 준다 —
            #   HTTP 403  {"rsp_cd":"EGW00103","rsp_msg":"유효하지 않은 client_id입니다."}
            # 그걸 버리고 "발급 실패" 만 내면 고객은 앱키가 틀린 건지, 서버가
            # 다른 건지, 권한이 없는 건지 구분할 수 없다. 받은 것을 그대로 전한다.
            try:
                d = r.json()
            except ValueError:
                d = None
            gw_code = (d.get("rsp_cd") or d.get("error") or d.get("error_code")
                       if isinstance(d, dict) else None)
            gw_msg = (_message(d) or str(d.get("error_description") or "").strip()
                      if isinstance(d, dict) else "")
            detail = (" ".join(x for x in (gw_code, gw_msg) if x)
                      # 본문 원문을 싣지 않는다 — 프록시가 돌려준 HTML 에는
                      # 중간 장비 이름과 내부 주소가 들어 있다.
                      or "응답을 해석할 수 없습니다")
            raise MeritzError(
                f"토큰 발급 실패 (HTTP {r.status_code}): {detail}\n"
                f"  요청한 서버: {self.s.base_url}\n"
                f"  앱키가 이 서버의 것인지 확인하세요. 다른 환경에서 발급받은"
                f" 앱키를 보내면 이 오류가 납니다."
                f" 운영은 MERITZ_BASE_URL={PROD} 입니다.",
                # code 는 AUTH_FAILED 로 유지한다 — 호출자가 분기하는 값이라
                # 게이트웨이 코드로 바꾸면 기존 처리가 갈라진다. 게이트웨이 코드는
                # 메시지와 body 로 전달된다.
                status=r.status_code, body=r.text, code="AUTH_FAILED")
        try:
            d = r.json()
            token, ttl = d["access_token"], int(d.get("expires_in", 43200))
        except (ValueError, KeyError, TypeError):
            raise MeritzError("토큰 응답을 해석할 수 없습니다.", status=r.status_code,
                              body=r.text[:300], code="AUTH_BAD_RESPONSE") from None

        with self._lock:
            self._token = token
            self._expires = time.time() + ttl - 60
            self._issued_for = self._cache_key
        return token


def _degraded_from_catalog() -> set[str]:
    """rsp_cd 가 손상돼 오는 API 목록. 카탈로그를 못 읽으면 빈 집합이다
    (빈 집합은 '아무것도 봐주지 않는다' 쪽이라 안전한 기본값이다)."""
    try:
        from .catalog import load_catalog
        fixes = load_catalog().corrections.get("corrections") or []
    except Exception as e:
        # 조용히 빈 집합이 되면 국내 시세 8종이 전부 실패로 돌아선다.
        # 안전한 방향이긴 하나 원인을 알 수 없으면 안 된다.
        warnings.warn(f"corrections.json 을 읽지 못했습니다: {e}. "
                      f"손상 응답 API 판정이 꺼집니다.", RuntimeWarning, stacklevel=2)
        return set()
    return {k for c in fixes if c.get("degraded") for k in c.get("keys", [])}


class _Throttle:
    """API 별 초당 호출 한도를 지킨다.

    카탈로그가 API 마다 TPS 를 알려 주는데도 지키지 않으면, 목록을 순회하는
    평범한 코드가 절반쯤 EGW00200 으로 실패한다.
    """

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def wait(self, key: str, tps: int) -> None:
        if tps <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                hits = [t for t in self._hits.get(key, []) if now - t < 1.0]
                if len(hits) < tps:
                    hits.append(now)
                    self._hits[key] = hits
                    return
                sleep_for = 1.0 - (now - hits[0])
            time.sleep(max(sleep_for, 0.01))


class ApiClient:
    def __init__(self, settings, tokens: TokenManager | None = None,
                 degraded_keys: set[str] | None = None):
        self.s = settings
        self.tokens = tokens or TokenManager(settings)
        # 응답 코드가 손상돼 오는 것이 확인된 API. corrections.json 에서 온다.
        #
        # 넘겨받지 못하면 카탈로그에서 직접 읽는다. 호출자가 챙겨야 하는 값으로
        # 두면, 잊은 쪽에서 정상 응답이 UNREADABLE_RESPONSE_CODE 로 튕긴다.
        self.degraded_keys = (
            degraded_keys if degraded_keys is not None else _degraded_from_catalog())
        # 매 호출마다 TLS 를 새로 맺으면 호출당 수십 ms 가 그냥 나간다.
        # Session 은 스레드 안전하지 않다. 도구 호출을 스레드로 넘기므로
        # 스레드마다 하나씩 둔다 — 커넥션 재사용은 그대로 얻는다.
        self._local = threading.local()
        self._throttle = _Throttle()

    @property
    def _http(self) -> requests.Session:
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = self._local.session = requests.Session()
        return sess

    def headers(self, api, extra: dict | None = None) -> dict:
        h = {"authorization": f"Bearer {self.tokens.get()}", "mac_address": mac_address()}
        if api.get("content_type"):
            h["content-type"] = api["content_type"]
        for k in ("tr_cont", "tr_cont_key"):
            v = (extra or {}).get(k)
            if v not in (None, ""):
                h[k] = str(v)
        return h

    @staticmethod
    def validate(api, params: dict) -> list[str]:
        """명세와 어긋나는 점을 모아 돌려준다.

        호출부(MCP 도구·CLI·예제)뿐 아니라 **라이브러리로 직접 쓸 때도**
        걸려야 한다. 검증을 호출부에만 두면 패키지를 임포트해 쓰는 코드에서
        무검증으로 주문이 나간다.

        이름과 필수 여부만 보면 부족하다. **형식이 틀려도 게이트웨이가 오류를
        내지 않기 때문이다.** 투자자매매동향에 from="2026-09-01" 을 넣으면
        rsp_cd "0000" 과 함께 기간이 무시된 2년치 655건이 온다. 고객은 그걸
        한 달치로 읽는다. 그래서 카탈로그에 이미 있는 length·설명으로
        **호출 전에** 형식을 본다.
        """
        problems = []
        known = {p["name"] for p in api.params}
        for k in params:
            if k not in _META_KEYS and k not in known:
                problems.append(f"명세에 없는 파라미터: {k}")
        for name in api.required:
            if params.get(name) in (None, ""):
                p = api.param(name) or {}
                problems.append(f"필수 파라미터 누락: {name} ({p.get('name_ko') or ''})")
        problems += ApiClient._format_problems(api, params)
        return problems

    @staticmethod
    def _format_problems(api, params: dict) -> list[str]:
        """값의 형식을 명세와 대조한다. 명세에 있는 파라미터만 본다."""
        problems = []
        for name, value in params.items():
            if name in _META_KEYS or value in (None, ""):
                continue
            p = api.param(name)
            if not p:
                continue                       # 이름 오류는 위에서 이미 잡았다
            ko = p.get("name_ko") or ""
            text = value if isinstance(value, str) else str(value)
            if _is_date_param(p) and not _DATE8.match(text):
                problems.append(
                    f"{name} ({ko}) 은 YYYYMMDD 8자리여야 합니다: {value!r}. "
                    f"하이픈이나 자릿수가 어긋나면 게이트웨이가 오류 대신 "
                    f"엉뚱한 기간(또는 빈 목록)을 정상 코드와 함께 돌려줍니다.")
                continue
            limit = _declared_length(p)
            if limit and len(text) > limit:
                problems.append(
                    f"{name} ({ko}) 이 명세 길이 {limit}자를 넘었습니다"
                    f"({len(text)}자): {value!r}")
        return problems

    def require_confirmation(self, api, params: dict) -> None:
        """확인 게이트. 상태변경 요청은 유효한 confirm_token 없이는 나가지 않는다.

        게이트를 라이브러리 계층에 둔다. 도구 계층에만 두면 ApiClient 를 직접
        쓰는 코드에서 주문이 무확인으로 나간다.

          pv = safety.preview(api, api.body_params(params), s.base_url, s.app_key)
          client.call(api, {**params, "confirm_token": pv["confirm_token"]})

        토큰이 없거나 맞지 않으면 NEEDS_CONFIRMATION 을 올린다. 그때 body 에
        미리보기가 실려 있어, 호출부는 그대로 사용자에게 보이면 된다.

        토큰은 1회용이라 **여기서만** 소비한다. 호출부가 따로 check_confirm 을
        부르면 여기 도달하기 전에 토큰이 없어진다.
        """
        if not is_state_changing(api):
            return
        body = api.body_params(params)
        why = check_confirm(api["key"], body, params.get("confirm_token"),
                            self.s.base_url, self.s.app_key)
        if why:
            raise MeritzError(why, code="NEEDS_CONFIRMATION",
                              body=preview(api, body, self.s.base_url, self.s.app_key))

    def call(self, api, params: dict, _retried: bool = False) -> dict:
        if api is None:
            raise MeritzError(
                "모르는 api_type 입니다. 카탈로그에 없는 이름을 넘겼습니다.",
                code="UNKNOWN_API_TYPE")
        # 조회 전용 모드는 라이브러리 계층에서 막는다. 도구 계층에만 두면
        # meritz 를 직접 임포트해 쓰는 코드에서 스위치가 무력해진다.
        if self.s.read_only and is_state_changing(api):
            raise MeritzError(
                f"'{api['name']}'{josa(api['name'])} 상태를 바꾸는 요청이라 "
                f"조회 전용 모드에서 막혀 있습니다. "
                f"MERITZ_READ_ONLY 를 끄면 사용할 수 있습니다.",
                code="READ_ONLY")
        problems = self.validate(api, params)
        if problems:
            raise MeritzError("요청 파라미터가 명세와 다릅니다: " + "; ".join(problems),
                              code="INVALID_PARAMS")
        self.require_confirmation(api, params)
        try:
            tps = int(api.get("tps") or 0)
        except (TypeError, ValueError):
            tps = 0
        self._throttle.wait(api["key"], tps)

        url = self.s.base_url + api["path"]
        headers = self.headers(api, params)
        # 네트워크 예외를 그대로 올리면 예제와 CLI 가 60줄 트레이스백으로 죽는다.
        # 고객에게는 "도구가 깨졌다" 로 읽히고, 무엇을 해야 하는지도 알 수 없다.
        # MeritzError 로 감싸 다른 실패와 같은 방식으로 처리되게 한다.
        try:
            if api["method"] == "GET":
                r = self._http.get(url, params=api.query_params(params),
                                   headers=headers, timeout=self.s.timeout)
            else:
                r = self._http.post(url, json=api.body_params(params),
                                    headers=headers, timeout=self.s.timeout)
        except requests.Timeout:
            raise MeritzError(
                f"{self.s.timeout}초 안에 응답이 오지 않았습니다: {url}\n"
                f"  잠시 뒤 다시 호출하세요. 상태변경 요청이었다면 재전송 전에 "
                f"조회 API 로 실제 처리 여부를 먼저 확인하십시오.",
                code="TIMEOUT") from None
        except requests.RequestException as e:
            raise MeritzError(
                f"서버에 연결하지 못했습니다: {e}\n"
                f"  요청한 서버: {self.s.base_url}\n"
                f"  네트워크와 MERITZ_BASE_URL 을 확인한 뒤 다시 호출하세요."
                f" (운영 {PROD})",
                code="UNREACHABLE") from None
        try:
            body = r.json()
        except ValueError:
            body = r.text

        # 토큰이 죽었으면 한 번만 다시 받아 재시도한다. 이걸 안 하면 포털에서
        # 토큰을 폐기했을 때 프로세스가 살아 있는 내내(최대 12시간) 전 호출이 실패한다.
        code = body.get("rsp_cd") if isinstance(body, dict) else None
        if code in TOKEN_DEAD and not _retried:
            log.info("토큰이 무효화됐습니다(%s). 다시 발급받아 재시도합니다.", code)
            self.tokens.invalidate()
            return self.call(api, params, _retried=True)

        out = self.judge(api, r.status_code, body, url, self.degraded_keys)
        log.info("%s %s %s → %s%s", api["method"], api["key"], api["tr_id"],
                 "ok" if out.get("ok") else out.get("error"),
                 f" {out.get('rsp_cd')}" if out.get("rsp_cd") else "")
        return out

    @staticmethod
    def judge(api, status: int, body: Any, url: str,
              degraded_keys: set[str] | None = None) -> dict:
        """응답을 판정한다.

        HTTP 200 만으로 성공을 단정하지 않는다. 다만 **읽히는 오류 코드를 손상으로
        오해하면 거절된 주문이 성공으로 보고된다.** 그래서 판정 순서를 이렇게 둔다.

          1) HTTP 실패            → 실패
          2) rsp_cd 가 읽히는가?
               예 → 성공 코드면 성공, 아니면 무조건 업무 오류
               아니오(손상) → 아래 3)
          3) 손상이 알려진 API 인가?
               아니오 → 실패 (모르는 손상을 성공으로 넘기지 않는다)
               예 → data 가 비어 있지 않으면 '검증 불가'로 통과 + 경고
                     상태변경 API 는 여기서도 통과시키지 않는다

        손상 API 목록은 corrections.json 에서 온다. 화이트리스트로 두는 이유는
        새 API 가 조용히 편승하지 못하게 하기 위해서다.
        """
        out: dict[str, Any] = {"ok": True, "http_status": status,
                               "api_type": api["key"], "tr_id": api["tr_id"], "url": url,
                               "body": body}
        if status != 200:
            out["ok"] = False
            code = body.get("rsp_cd") if isinstance(body, dict) else None
            if code in RETRYABLE:
                # 초당 거래건수 초과가 429 가 아니라 500 으로 온다. HTTP_ERROR 로만
                # 보고하면 호출자가 재시도해도 되는지 알 수 없다.
                out["error"] = "RATE_LIMITED"
                out["rsp_cd"] = code
                out["retryable"] = True
                out["message"] = _message(body, "초당 거래건수를 초과했습니다.")
                out["note"] = ("이 API 의 초당 호출 한도는 "
                               f"{api.get('tps') or '?'} 건입니다. 간격을 두고 다시 부르세요.")
                return out
            # 이 게이트웨이는 업무 오류도 HTTP 500 으로 보낸다. 상태코드만
            # 보고 HTTP_ERROR 로 묶으면 "권한 없음"·"필수값 누락" 처럼 고칠 수
            # 있는 사유가 전송 장애와 같은 칸에 들어간다. 읽히는 rsp_cd 가
            # 있으면 업무 오류로 가른다.
            out["error"] = "BUSINESS_ERROR" if (isinstance(body, dict) and code) else "HTTP_ERROR"
            if isinstance(body, dict) and code:
                out["rsp_cd"] = code
                out["message"] = _message(body, f"업무 오류 {code}")
                _attach_sub_msg(out, body)
            else:
                # JSON 이 아닌 응답(프록시가 돌려준 HTML 등)에는 rsp_cd 가 없다.
                # 그 본문을 화면에 그대로 옮기지 않는다 — 중간 장비의 제품명과
                # 내부 호스트가 섞여 나올 수 있고, 이용자에게는 쓸모도 없다.
                out["message"] = (
                    f"서버가 일시적으로 응답하지 못했습니다(HTTP {status}). "
                    f"잠시 뒤 다시 시도해 주십시오. 같은 결과가 이어지면 "
                    f"개발자 포털 문의 채널로 알려 주십시오.")
            return out
        if not isinstance(body, dict):
            # HTTP 200 인데 JSON 이 아니다. 성공으로 넘기면 호출자가 곧바로
            # body.get(...) 을 하다가 원인을 알 수 없는 오류로 죽는다.
            out["ok"] = False
            out["error"] = "BAD_RESPONSE"
            out["message"] = ("서버가 예상과 다른 형식으로 응답했습니다. "
                              "잠시 뒤 다시 시도해 주십시오.")
            return out

        code = body.get("rsp_cd")
        readable = isinstance(code, str) and code.isprintable() and code.strip() != ""

        if readable:
            if code in OK_CODES:
                # 정상 코드로 와도 rsp_sub_msg 에 입력 검증 사유가 담겨 오는
                # API 가 있다(예: 국내 실현손익(종목별)의 "종료일자보다 클 수
                # 없습니다"). 버리지 않고 남긴다.
                _attach_sub_msg(out, body)
                warned = _warning_row(body, _warn_scheme(api))
                if warned:
                    # rsp_cd 만 보면 "주문이 완료되었습니다" 로 성공처럼 보인다.
                    # 실제로는 접수되지 않았고, 재전송하지 않으면 아무 일도 안 일어난다.
                    out["ok"] = False
                    out["error"] = "ORDER_NOT_ACCEPTED"
                    code, why, resendable = warned
                    out["warn_cls_code"] = code
                    out["resendable"] = resendable
                    tail = ('내용을 사용자에게 보이고 동의를 받은 뒤 warn_cnfr_yn="Y" 로 '
                            "같은 주문을 다시 보내야 접수됩니다."
                            if resendable else
                            "보류 상태라 같은 주문을 다시 보내도 접수되지 않습니다. "
                            "사유를 해소한 뒤 새로 주문하십시오.")
                    sub = out.get("rsp_sub_msg")
                    out["message"] = (
                        f"주문이 접수되지 않았습니다 (warn_cls_code={code}). "
                        f"{why or '경고 확인이 필요한 주문입니다.'}"
                        f"{f' — {sub}' if sub else ''} {tail}")
                    return out
                if code == "0001":
                    out["note"] = ("주문이 접수됐습니다. 접수 성공일 뿐 체결·수리 확정이 "
                                   "아닙니다. 주문내역 조회로 최종 상태를 확인하세요.")
                elif code in NO_DATA_CODES:
                    # **코드가 아니라 data 로 판정한다.** 이 게이트웨이는 자료를
                    # 담고도 5820 을 보낸다(담보 현황·국내 실현손익(종목별)·
                    # 해외 실현손익). 코드만 보고 no_data 를 달면 있는 자료가
                    # "자료 없음" 으로 보고된다.
                    out["no_data"] = _payload_is_empty(body)
                    if out["no_data"]:
                        out["note"] = ("조회 조건에 해당하는 자료가 없습니다. "
                                       "오류가 아닙니다.")
                    else:
                        out["note"] = (
                            f'응답 코드는 "자료 없음"({code})이지만 data 에 자료가 '
                            f"들어 있습니다. 이 게이트웨이의 알려진 동작이며, "
                            f"자료가 정상이므로 그대로 쓰시면 됩니다.")
                elif code == "5762":
                    out["note"] = ("다음 페이지가 있습니다. tr_cont=1 과 tr_cont_key 로 "
                                   "이어서 조회하세요.")
                sub = out.get("rsp_sub_msg")
                if sub:
                    # 국내 실현손익(종목별)에 시작일 > 종료일 을 넣으면
                    # rsp_cd "0000" 과 함께 rsp_sub_msg "종료일자보다 클 수 없습니다."
                    # 가 온다. 이걸 버리면 고객은 잘못된 조회를 성공으로 읽는다.
                    tail = (f'게이트웨이가 상세 메시지를 함께 보냈습니다 — "{sub}". '
                            f"정상 코드로 와도 입력 검증 사유가 여기에 담기므로 "
                            f"요청 값을 확인하십시오.")
                    out["note"] = f"{out['note']} {tail}" if out.get("note") else tail
                return out
            out["ok"] = False
            out["error"] = "BUSINESS_ERROR"
            out["rsp_cd"] = code
            out["message"] = _message(body, f"업무 오류 {code}")
            _attach_sub_msg(out, body)
            return out

        # 여기부터는 rsp_cd 를 읽을 수 없는 경우
        out["rsp_cd_raw"] = repr(code)
        if api["key"] not in (degraded_keys or set()):
            out["ok"] = False
            out["error"] = "UNREADABLE_RESPONSE_CODE"
            out["message"] = ("응답을 해석할 수 없어 성공으로 처리하지 않았습니다. 요청 값을 "
                              "확인하신 뒤 다시 시도해 주십시오.")
            return out
        if is_state_changing(api):
            out["ok"] = False
            out["error"] = "UNVERIFIABLE_STATE_CHANGE"
            out["message"] = ("응답 코드를 읽을 수 없어 처리 결과를 확인할 수 없습니다. "
                              "상태변경 요청이므로 성공으로 보고하지 않습니다. "
                              "조회 API 로 실제 상태를 확인하세요.")
            return out

        data = body.get("data")
        if _is_empty(data):
            out["ok"] = False
            out["error"] = "EMPTY_RESULT"
            out["message"] = ("응답 코드를 읽을 수 없고 데이터도 비어 있습니다. "
                              "종목코드·조회조건을 확인하세요. "
                              "(이 API 는 잘못된 입력에도 오류 대신 빈 값을 돌려줍니다)")
            return out

        out["verified"] = False
        out["warning"] = ("이 API 는 응답 코드로 성공을 판정할 수 없습니다. 데이터는 "
                          "그대로 쓰시면 됩니다.")
        return out


# 연속조회가 **실제로 동작하는 것이 확인된** API.
#
# 카탈로그에서 tr_cont/tr_cont_key 를 선언한 API 는 13건이지만, 이어서 조회하면
# 실제로 다음 구간이 오는 것은 아래 4건뿐이다. 나머지에 순회를 걸면
# 같은 구간을 반복하거나(무한 반복) 거짓 0건이 나온다. 그래서 화이트리스트로 둔다 —
# 새 API 가 조용히 편승하지 못하게 하기 위해서다.
PAGING_OK = frozenset({
    "transactions",          # 내 계좌 입출금 내역
    "orders_history",        # 국내 주문내역
    "ovs_orders_history",    # 해외 주문체결내역
    "ref_ovs_exchanges",     # 해외 거래소 목록
})

# 다음 페이지가 있다는 신호
PAGE_MORE = "5762"


def paginate(client: ApiClient, api, params: dict,
             max_pages: int = 100) -> dict:
    """연속조회를 끝까지 돌려 data 를 이어 붙인다.

    직접 짜면 틀리기 쉬운 세 가지를 여기서 막는다.

      ① 연속조회 키를 **그대로** 보낸다. 오른쪽 공백이 붙어 내려오는데 잘라내면
         서버가 다른 구간을 주거나 조용히 0건을 준다.
      ② 키가 **바뀌지 않으면 멈춘다.** 같은 키를 다시 보내면 같은 구간이
         영원히 돌아온다. 페이지 수 상한만으로는 100번 돌 때까지 못 알아챈다.
      ③ 실제로 연속조회가 되는 API 인지 먼저 본다(PAGING_OK).

    돌려주는 것 — {"ok", "rows", "pages", "truncated", "note"}.
    truncated 가 True 면 **끝까지 받지 못한 것**이다. 건수를 합계로 쓰기 전에
    반드시 확인해야 한다.
    """
    if api["key"] not in PAGING_OK:
        raise MeritzError(
            f"'{api['name']}'{josa(api['name'])} 연속조회가 동작하지 않는 API 입니다. "
            f"명세에 tr_cont 가 있어도 다음 구간이 오지 않아 같은 구간이 반복됩니다. "
            f"call() 로 한 번만 조회하세요. "
            f"연속조회가 되는 것은 {' · '.join(sorted(PAGING_OK))} 입니다.",
            code="PAGING_UNSUPPORTED")

    rows: list = []
    seen_keys: set[str] = set()
    page = 0
    params = {k: v for k, v in params.items() if k not in ("tr_cont", "tr_cont_key")}
    request = dict(params)
    truncated, note = False, ""

    while True:
        res = client.call(api, request)
        if not res["ok"]:
            raise MeritzError(
                f"{page + 1}번째 페이지에서 실패했습니다: "
                f"{res.get('error')} — {res.get('message')}",
                status=res.get("http_status"), body=res.get("body"),
                code=res.get("error"))
        body = res["body"]
        if not isinstance(body, dict):
            raise MeritzError(f"JSON 이 아닌 응답입니다: {str(body)[:200]}",
                              code="BAD_RESPONSE")
        data = body.get("data")
        rows += data if isinstance(data, list) else ([data] if data else [])
        page += 1

        if body.get("rsp_cd") != PAGE_MORE:
            break
        key = body.get("tr_cont_key")            # 공백까지 원본 그대로 쓴다
        if key in (None, "") or key in seen_keys:
            truncated = True
            note = ("서버가 연속조회 키를 바꾸지 않아 중단했습니다. "
                    "받은 건수는 전체가 아닙니다 — 조회 기간을 나눠 다시 받으세요.")
            break
        if page >= max_pages:
            truncated = True
            note = (f"{max_pages}페이지에서 중단했습니다. 받은 건수는 전체가 아닙니다 — "
                    f"조회 기간을 나누거나 max_pages 를 올리세요.")
            break
        seen_keys.add(key)
        request = dict(params, tr_cont="1", tr_cont_key=key)

    return {"ok": True, "rows": rows, "pages": page,
            "truncated": truncated, "note": note}
