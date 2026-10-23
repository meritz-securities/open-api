"""볼린저밴드.

표준 정의: 중심선은 SMA, 상·하단은 중심선 ± num_std × 표준편차(모집단).
종가가 어느 구간에 있는지만 판정한다 — ABOVE_UPPER · INSIDE · BELOW_LOWER.
데이터: market_candles_days 종가.  계산: indicators.calc.bollinger

실행:  python -m indicators.bollinger            # 데모
       python -m indicators.bollinger 005930     # 실데이터

⚠️ 투자자문 아님. 시그널만 산출한다.
"""
from __future__ import annotations

import sys

from indicators.calc import bollinger
from indicators.candles import closes, load_candles, sample_candles
from indicators.notice import NOTICE


def evaluate(candles: list[dict], period: int = 20, num_std: float = 2.0,
             mode: str = "reversion") -> dict:
    px = closes(candles)
    if len(px) < period:
        return {"state": "NO_SIGNAL", "reason": f"데이터 부족(밴드 {period}일 미만)", "detail": {}}
    band = bollinger(px, period, num_std)
    close, upper, lower = px[-1], band["upper"][-1], band["lower"][-1]
    below, above = close < lower, close > upper
    # below 와 above 는 상호배타다. 상태는 관찰 사실이므로 해석 모드가 없다.
    state = "BELOW_LOWER" if below else "ABOVE_UPPER" if above else "INSIDE"
    reason = ("하단밴드 이탈" if below else "상단밴드 이탈" if above else "밴드 내부") \
        + f" (종가 {close:.0f}, 밴드 {lower:.0f}~{upper:.0f})"
    return {"state": state, "reason": reason,
            "detail": {"close": close, "upper": round(upper, 1),
                       "lower": round(lower, 1), "mid": round(band["mid"][-1], 1)}}


def main(argv: list[str]) -> None:
    print(NOTICE)
    if len(argv) > 1:
        candles = load_candles(argv[1])
        src = f"실데이터 {argv[1]} ({len(candles)}봉)"
    else:
        candles = sample_candles()
        src = f"데모 합성데이터 ({len(candles)}봉)"
    result = evaluate(candles)
    print(f"[볼린저밴드] {src}")
    print(f"  지표: {result['state']}  —  {result['reason']}")
    print(f"  상세  : {result['detail']}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        # 앱키가 그 서버의 것이 아니면 여기로 온다. 메시지에 원인이
        # 들어 있으므로 트레이스백 대신 한 줄로 끝낸다.
        raise SystemExit(f"{e}") from None
