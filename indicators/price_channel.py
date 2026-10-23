"""돈치안 채널 — 직전 n봉의 최고·최저를 넘었는지.

표준 정의: 최근 n봉의 고가 최대와 저가 최소로 띠를 만들고, 종가가 그 띠를
벗어나면 돌파라고 부른다. 널리 알려진 추세추종 규칙의 원형이다.
띠는 **당일을 뺀** 직전 n봉으로 만든다. 당일 고가를 넣으면 신고가인 날은
언제나 상단과 같아져 돌파가 영영 성립하지 않기 때문이다.
데이터: market_candles_days(국내 일봉)의 고가·저가·종가.  계산: indicators.calc.donchian

실행:  python -m indicators.price_channel            # 데모(네트워크 불필요)
       python -m indicators.price_channel 005930    # 실데이터(앱키·망 필요)

⚠️ 투자자문 아님. 시그널만 산출하며 자동 주문하지 않는다. (README의 고지 참조)
"""
from __future__ import annotations

import sys

from indicators.calc import donchian
from indicators.candles import load_candles, sample_candles
from indicators.notice import NOTICE


def evaluate(candles: list[dict], period: int = 20) -> dict:
    if len(candles) < period + 1:
        return {"state": "NO_SIGNAL", "reason": f"데이터 부족({period + 1}봉 미만)", "detail": {}}
    ch = donchian([c["high"] for c in candles], [c["low"] for c in candles], period)
    up, lo = ch["upper"][-1], ch["lower"][-1]
    if up is None or lo is None:
        return {"state": "NO_SIGNAL", "reason": "채널 계산 구간이 모자랍니다", "detail": {}}
    close = candles[-1]["close"]

    if close > up:
        state, reason = "BREAK_UPPER", f"종가 {close:,.0f} 가 {period}봉 최고 {up:,.0f} 위"
    elif close < lo:
        state, reason = "BREAK_LOWER", f"종가 {close:,.0f} 가 {period}봉 최저 {lo:,.0f} 아래"
    else:
        state, reason = "INSIDE", f"종가 {close:,.0f} 가 채널 {lo:,.0f}~{up:,.0f} 안"
    width = (up - lo) / close * 100 if close else 0.0
    return {"state": state, "reason": reason,
            "detail": {"upper": round(up), "lower": round(lo), "close": close,
                       "width_pct": round(width, 2),
                       "position_pct": round((close - lo) / (up - lo) * 100, 1) if up > lo else None}}


def main(argv: list[str]) -> None:
    print(NOTICE)
    if len(argv) > 1:
        candles = load_candles(argv[1])
        src = f"실데이터 {argv[1]} ({len(candles)}봉)"
    else:
        candles = sample_candles()
        src = f"데모 합성데이터 ({len(candles)}봉)"
    r = evaluate(candles)
    print(f"[돈치안 채널] {src}")
    print(f"  지표: {r['state']}  —  {r['reason']}")
    print(f"  상세  : {r['detail']}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        raise SystemExit(f"{e}") from None
