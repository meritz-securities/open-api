"""ATR — 변동성 크기.

표준 정의: True Range = max(고−저, |고−전일종가|, |저−전일종가|).
ATR 은 그 TR 을 Wilder 방식으로 평활한 값이다. 방향이 아니라 **변동폭**을 잰다.

데이터: market_candles_days(국내 일봉)의 hprc·lprc·stck_clpr.  계산: indicators.calc.atr
실행:  python -m indicators.atr            # 데모
       python -m indicators.atr 005930     # 실데이터

현재 ATR 이 최근 구간 평균보다 큰지 작은지만 판정한다.

⚠️ 투자자문 아님. 시그널만 산출하며 자동 주문하지 않는다. (README의 고지 참조)
"""
from __future__ import annotations

import sys

from indicators.calc import atr
from indicators.candles import load_candles, sample_candles
from indicators.notice import NOTICE


def evaluate(candles: list[dict], period: int = 14, window: int = 20) -> dict:
    if len(candles) < period + 2:
        return {"state": "NO_SIGNAL", "reason": f"데이터 부족({period + 2}봉 미만)", "detail": {}}
    hi = [c["high"] for c in candles]
    lo = [c["low"] for c in candles]
    cl = [c["close"] for c in candles]
    a = atr(hi, lo, cl, period)
    cur = a[-1]
    if cur is None:
        return {"state": "NO_SIGNAL", "reason": "ATR 계산 구간이 모자랍니다", "detail": {}}

    hist = [x for x in a[-window:] if x is not None]
    avg = sum(hist) / len(hist)
    if cur > avg * 1.2:
        state, reason = "VOLATILITY_HIGH", f"ATR 이 최근 {len(hist)}봉 평균보다 큽니다"
    elif cur < avg * 0.8:
        state, reason = "VOLATILITY_LOW", f"ATR 이 최근 {len(hist)}봉 평균보다 작습니다"
    else:
        state, reason = "VOLATILITY_NORMAL", f"ATR 이 최근 {len(hist)}봉 평균 수준입니다"

    close = cl[-1]
    return {"state": state,
            "reason": f"{reason} (ATR {cur:,.0f} / 평균 {avg:,.0f})",
            "detail": {"atr": round(cur, 1), "atr_avg": round(avg, 1),
                       "close": close,
                       "atr_pct": round(cur / close * 100, 2) if close else None}}


def main(argv: list[str]) -> None:
    print(NOTICE)
    if len(argv) > 1:
        candles, src = load_candles(argv[1]), f"실데이터 {argv[1]}"
    else:
        candles, src = sample_candles(), "데모 합성데이터"
    r = evaluate(candles)
    print(f"[ATR] {src} ({len(candles)}봉)")
    print(f"  지표: {r['state']}  —  {r['reason']}")
    print(f"  상세  : {r['detail']}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        raise SystemExit(f"{e}") from None
