"""종목코드·거래소코드 변환.

**API 마다 같은 것을 다른 코드로 부른다.** 고객이 가장 자주 막히는 지점이고,
틀려도 오류가 나지 않고 조용히 빈 데이터가 오기 때문에 알아채기도 어렵다.

  국내 종목코드   시세는 "005930", 주문은 "A005930"
  해외 거래소     주문·예약주문은 ovrs_exch_vndr_idno("0537")
                  실현손익·시세는 ovrs_stck_exch_idno("OQ")

여기서 규격을 새로 만들지 않는다. 표는 해외 거래소 조회
(/reference/v1/overseas/exchanges)가 돌려주는 두 필드를 그대로 옮긴 것이고,
load_exchanges() 로 그 API 에서 다시 채울 수 있다.
"""
from __future__ import annotations

import re

# ------------------------------------------------------------- 국내 종목코드
_DOMESTIC = re.compile(r"^([A-Za-z]?)(\d{6})$")


def normalize_iscd(iscd: str, *, prefix: bool) -> str:
    """국내 종목코드에서 앞의 한 글자를 붙이거나 뗀다.

      normalize_iscd("005930", prefix=True)   -> "A005930"   주문용
      normalize_iscd("A005930", prefix=False) -> "005930"    시세용

    시세에 "A005930" 을 넣으면 오류 대신 값이 전부 0 인 껍데기가 오고,
    주문에 "005930" 을 넣으면 종목을 찾지 못한다. 둘 다 조용히 틀린다.

    해외 종목코드(거래소 접미어가 붙는다)와 6자리가 아닌 값은 손대지 않고
    그대로 돌려준다 — 모르는 형식을 고쳐 쓰면 더 나쁜 일이 생긴다.
    """
    m = _DOMESTIC.match(str(iscd or "").strip())
    if not m:
        return iscd
    head, digits = m.groups()
    if prefix:
        return f"{head or 'A'}{digits}"
    return digits


# ------------------------------------------------------------- 해외 거래소
# 해외 거래소 조회 응답을 2026-09-11 에 그대로 받아 적은 것.
# onln_yn="Y" 인 거래소만 담았다 — 나머지는 주문이 나가지 않는다.
#
#   exch_id  ovrs_stck_exch_idno   실현손익·시세가 쓴다
#   vendor   ovrs_exch_vndr_idno   주문·예약주문·휴장일이 쓴다
EXCHANGES: tuple[tuple[str, str, str], ...] = (
    # exch_id, vendor, 이름
    ("OQ", "0537", "나스닥"),
    ("NY", "0321", "뉴욕"),
    ("AX", "0066", "아멕스"),
    ("HK", "0104", "홍콩"),
    ("SH", "0215", "상해"),
    ("SZ", "0214", "심천"),
    ("JP", "0106", "도쿄"),
)

_TO_VENDOR = {e: v for e, v, _ in EXCHANGES}
_TO_EXCH_ID = {v: e for e, v, _ in EXCHANGES}


def to_vendor_code(code: str) -> str | None:
    """거래소 코드를 주문이 쓰는 벤더 코드로 바꾼다("OQ" -> "0537").

    이미 벤더 코드면 그대로 돌려준다. 모르는 코드는 None 이다 —
    **추측해서 넘기면 주문이 다른 거래소로 나간다.**
    """
    c = str(code or "").strip().upper()
    if c in _TO_EXCH_ID:
        return c
    return _TO_VENDOR.get(c)


def to_exchange_id(code: str) -> str | None:
    """벤더 코드를 실현손익·시세가 쓰는 거래소 코드로 바꾼다("0537" -> "OQ").

    이미 거래소 코드면 그대로 돌려준다. 모르는 코드는 None 이다 —
    틀린 코드를 넣으면 오류 없이 빈 목록이 온다.
    """
    c = str(code or "").strip().upper()
    if c in _TO_VENDOR:
        return c
    return _TO_EXCH_ID.get(c)


def load_exchanges(client, catalog) -> tuple[tuple[str, str, str], ...]:
    """해외 거래소 조회에서 표를 다시 채운다. 거래소가 늘거나 코드가 바뀔 때 쓴다.

    돌려주는 값은 EXCHANGES 와 같은 모양이고, 이 모듈의 변환표도 함께 갱신된다.
    """
    from .client import paginate

    api = catalog.get("ref_ovs_exchanges")
    if api is None:
        raise LookupError("카탈로그에 ref_ovs_exchanges 가 없습니다.")
    rows = paginate(client, api, {})["rows"]
    table = tuple(
        (str(r.get("ovrs_stck_exch_idno") or "").strip(),
         str(r.get("ovrs_exch_vndr_idno") or "").strip(),
         str(r.get("kor_name") or "").strip())
        for r in rows
        if r.get("onln_yn") == "Y" and r.get("ovrs_stck_exch_idno")
        and r.get("ovrs_exch_vndr_idno"))
    global EXCHANGES
    EXCHANGES = table
    _TO_VENDOR.clear()
    _TO_EXCH_ID.clear()
    _TO_VENDOR.update({e: v for e, v, _ in table})
    _TO_EXCH_ID.update({v: e for e, v, _ in table})
    return table
