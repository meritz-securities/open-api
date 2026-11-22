"""RSI 과매수·과매도 구간 판정.

표준 정의: RSI(14) 가 30 미만이면 과매도(OVERSOLD), 70 초과면 과매수(OVERBOUGHT)
구간이라고 부른다. 현재 RSI 가 어느 구간인지만 판정한다.
데이터: market_candles_days 종가.  계산: indicators.calc.rsi

실행:  python -m indicators.rsi_reversal            # 데모
       python -m indicators.rsi_reversal 005930     # 실데이터

⚠️ 투자자문 아님. 시그널만 산출한다.
"""
from __future__ import annotations

import sys

from indicators.calc import rsi
from indicators.candles import closes, load_candles, sample_candles
from indicators.notice import NOTICE


def evaluate(candles: list[dict], period: int = 14,
             low: float = 30.0, high: float = 70.0) -> dict:
    px = closes(candles)
    series = rsi(px, period)
    latest = series[-1] if series else None
    if latest is None:
        return {"state": "NO_SIGNAL", "reason": f"데이터 부족(RSI {period} 계산 불가)", "detail": {}}
    if latest < low:
        state, reason = "OVERSOLD", f"RSI 하단 이탈: RSI {latest:.1f} < {low:.0f}"
    elif latest > high:
        state, reason = "OVERBOUGHT", f"RSI 상단 이탈: RSI {latest:.1f} > {high:.0f}"
    else:
        state, reason = "NEUTRAL", f"중립 구간: RSI {latest:.1f}"
    return {"state": state, "reason": reason,
            "detail": {"rsi": round(latest, 1), "close": px[-1]}}


def main(argv: list[str]) -> None:
    print(NOTICE)
    if len(argv) > 1:
        candles = load_candles(argv[1])
        src = f"실데이터 {argv[1]} ({len(candles)}봉)"
    else:
        candles = sample_candles()
        src = f"데모 합성데이터 ({len(candles)}봉)"
    result = evaluate(candles)
    print(f"[RSI 반전] {src}")
    print(f"  지표: {result['state']}  —  {result['reason']}")
    print(f"  상세  : {result['detail']}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        # 앱키가 그 서버의 것이 아니면 여기로 온다. 메시지에 원인이
        # 들어 있으므로 트레이스백 대신 한 줄로 끝낸다.
        raise SystemExit(f"{e}") from None
