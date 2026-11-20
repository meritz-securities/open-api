"""호가 잔량 불균형.

종가 시계열로는 존재하지 않는 층위다. 호가 조회 응답에는 10단계 잔량과
**그 증감**이 함께 온다.

  total_askp_rsqn / total_bidp_rsqn   총 매도·매수 잔량
  total_ntby_rsqn                     총 순매수 잔량 (서버가 매수-매도를 계산해 준다)
  total_askp_rsqn_icdc / total_bidp_rsqn_icdc   총 잔량 증감
  askp_rsqn1~10 / bidp_rsqn1~10       단계별 잔량

데이터: market_orderbook(호가 조회) 1회 — 스냅숏이다. 시계열이 필요하면 반복 호출한다.
실행:  python -m indicators.orderbook_imbalance            # 데모
       python -m indicators.orderbook_imbalance 005930     # 실데이터

mdtm_prc(중간가격)는 최우선 호가가 있어도 0 으로 내려오므로 쓰지 않는다.
중간가는 askp·bidp 로 직접 구한다.

⚠️ 투자자문 아님. 시그널만 산출하며 자동 주문하지 않는다. (README의 고지 참조)
"""
from __future__ import annotations

import sys

from indicators.notice import NOTICE

HEAVY = 0.20        # 불균형 판정 문턱 (총잔량 대비 비율)


def evaluate(book: dict, heavy: float = HEAVY) -> dict:
    ask = _num(book.get("total_askp_rsqn"))
    bid = _num(book.get("total_bidp_rsqn"))
    if not ask or not bid:
        return {"state": "NO_SIGNAL", "reason": "총 잔량이 응답에 없습니다", "detail": {}}

    total = ask + bid
    ratio = (bid - ask) / total            # +면 매수잔량 우위
    if ratio > heavy:
        state, reason = "BID_HEAVY", "매수 잔량 우위"
    elif ratio < -heavy:
        state, reason = "ASK_HEAVY", "매도 잔량 우위"
    else:
        state, reason = "BALANCED", "균형"

    d_ask = _num(book.get("total_askp_rsqn_icdc")) or 0.0
    d_bid = _num(book.get("total_bidp_rsqn_icdc")) or 0.0
    if d_bid > d_ask:
        trend = "BID_BUILDING"
    elif d_ask > d_bid:
        trend = "ASK_BUILDING"
    else:
        trend = "STABLE"

    front = _front_load(book)
    detail = {"총매수잔량": bid, "총매도잔량": ask, "불균형": round(ratio, 4),
              "증감_매수": d_bid, "증감_매도": d_ask, "잔량증감": trend}
    ntby = _num(book.get("total_ntby_rsqn"))
    if ntby is not None:
        detail["서버_총순매수잔량"] = ntby
    if front is not None:
        detail["1~3단계_비중"] = round(front, 4)
    return {"state": state,
            "reason": f"{reason} (불균형 {ratio:+.1%}, 잔량증감 {trend})",
            "detail": detail}


def _front_load(book: dict):
    """1~3단계 잔량이 10단계 전체에서 차지하는 비중. 앞단에 몰렸는지 본다."""
    tot = front = 0.0
    for side in ("askp_rsqn", "bidp_rsqn"):
        for i in range(1, 11):
            v = _num(book.get(f"{side}{i}"))
            if v is None:
                continue
            tot += v
            if i <= 3:
                front += v
    return front / tot if tot else None


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def load_orderbook(iscd: str, mkt: str = "J") -> dict:
    from meritz import client, load_catalog

    cat, c = load_catalog(), client()
    res = c.call(cat.get("market_orderbook"), {"mrkt_div_code": mkt, "iscd": iscd})
    if not res["ok"]:
        raise RuntimeError(f"{res.get('error')}: {res.get('message')}")
    return res["body"].get("data") or {}


def sample_orderbook() -> dict:
    b = {"total_askp_rsqn": 1847741, "total_bidp_rsqn": 641688,
         "total_ntby_rsqn": -1206053, "total_askp_rsqn_icdc": -2,
         "total_bidp_rsqn_icdc": 0, "askp": 280500, "bidp": 280000}
    for i in range(1, 11):
        b[f"askp_rsqn{i}"] = 39709 + i * 1500
        b[f"bidp_rsqn{i}"] = 67648 - i * 2000
    return b


def main(argv: list[str]) -> None:
    print(NOTICE)
    if len(argv) > 1:
        book, src = load_orderbook(argv[1]), f"실데이터 {argv[1]}"
    else:
        book, src = sample_orderbook(), "데모 합성데이터"
    r = evaluate(book)
    print(f"[호가 잔량 불균형] {src}")
    print(f"  지표: {r['state']}  —  {r['reason']}")
    print(f"  상세  : {r['detail']}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        raise SystemExit(f"{e}") from None
