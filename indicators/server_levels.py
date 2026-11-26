"""서버가 계산해 주는 지지·저항 구간.

메리츠 Open API 의 현재가 조회 응답에는 피벗 포인트와 데마크 지지·저항이
**이미 계산되어** 들어 있다. 직접 구할 필요가 없다.

  pvt_pont      피벗포인트          dmrs  데마크 저항
  pvt_fst_dmrs  1차 저항            dmsp  데마크 지지
  pvt_scnd_dmrs 2차 저항
  pvt_fst_dmsp  1차 지지
  pvt_scnd_dmsp 2차 지지

데이터: market_prices(현재가 조회) 1회.
실행:  python -m indicators.server_levels            # 데모
       python -m indicators.server_levels 005930     # 실데이터

이 예제는 현재가가 어느 구간에 있는지만 판정한다.

⚠️ 투자자문 아님. 시그널만 산출하며 자동 주문하지 않는다. (README의 고지 참조)
"""
from __future__ import annotations

import sys

from indicators.notice import NOTICE

LEVELS = ("pvt_scnd_dmrs", "pvt_fst_dmrs", "pvt_pont", "pvt_fst_dmsp", "pvt_scnd_dmsp")
LABEL = {"pvt_scnd_dmrs": "2차저항", "pvt_fst_dmrs": "1차저항", "pvt_pont": "피벗",
         "pvt_fst_dmsp": "1차지지", "pvt_scnd_dmsp": "2차지지"}


def evaluate(quote: dict) -> dict:
    price = _num(quote.get("stck_prpr"))
    lv = {k: _num(quote.get(k)) for k in LEVELS}
    if price is None or any(v is None for v in lv.values()):
        return {"state": "NO_SIGNAL", "reason": "현재가 또는 지지·저항 값이 응답에 없습니다", "detail": {}}

    if price > lv["pvt_scnd_dmrs"]:
        state, reason = "ABOVE_R2", "2차 저항 위"
    elif price > lv["pvt_fst_dmrs"]:
        state, reason = "BETWEEN_R1_R2", "1차 저항과 2차 저항 사이"
    elif price > lv["pvt_pont"]:
        state, reason = "ABOVE_PIVOT", "피벗과 1차 저항 사이"
    elif price > lv["pvt_fst_dmsp"]:
        state, reason = "BELOW_PIVOT", "1차 지지와 피벗 사이"
    elif price > lv["pvt_scnd_dmsp"]:
        state, reason = "BETWEEN_S1_S2", "2차 지지와 1차 지지 사이"
    else:
        state, reason = "BELOW_S2", "2차 지지 아래"

    detail = {"close": price, **{LABEL[k]: v for k, v in lv.items()}}
    dm_r, dm_s = _num(quote.get("dmrs")), _num(quote.get("dmsp"))
    if dm_r is not None:
        detail["데마크저항"] = dm_r
    if dm_s is not None:
        detail["데마크지지"] = dm_s
    return {"state": state, "reason": f"{reason} (현재가 {price:,.0f})", "detail": detail}


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def load_quote(iscd: str, mkt: str = "J") -> dict:
    from meritz import client, load_catalog  # 지연 임포트(데모는 네트워크 불필요)

    cat, c = load_catalog(), client()
    res = c.call(cat.get("market_prices"), {"mrkt_div_code": mkt, "iscd": iscd})
    if not res["ok"]:
        raise RuntimeError(f"{res.get('error')}: {res.get('message')}")
    return res["body"].get("data") or {}


def sample_quote() -> dict:
    """데모용. 실제 응답에서 값의 크기 관계만 본뜬 합성 자료."""
    return {"stck_prpr": 280250, "pvt_scnd_dmrs": 282666, "pvt_fst_dmrs": 278832,
            "pvt_pont": 271166, "pvt_fst_dmsp": 267332, "pvt_scnd_dmsp": 259666,
            "dmrs": 280750, "dmsp": 269250}


def main(argv: list[str]) -> None:
    print(NOTICE)
    if len(argv) > 1:
        quote = load_quote(argv[1])
        src = f"실데이터 {argv[1]}"
    else:
        quote = sample_quote()
        src = "데모 합성데이터"
    r = evaluate(quote)
    print(f"[서버 제공 지지·저항] {src}")
    print(f"  지표: {r['state']}  —  {r['reason']}")
    print(f"  상세  : {r['detail']}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        raise SystemExit(f"{e}") from None
