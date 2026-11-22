"""스토캐스틱 슬로우 — 기간 고저 범위에서 종가가 어디에 있는지.

표준 정의: %K 는 최근 n봉의 고저 범위를 0~100 으로 놓고 종가의 위치를 재고,
%D 는 그 %K 의 이동평균이다. 통상 80 이상을 과매수, 20 이하를 과매도라 부른다.
RSI 와 달리 **종가가 아니라 고가·저가까지** 쓰므로 장중 변동을 반영한다.
데이터: market_candles_days(국내 일봉)의 고가·저가·종가.  계산: indicators.calc.stochastic

실행:  python -m indicators.stochastic            # 데모(네트워크 불필요)
       python -m indicators.stochastic 005930    # 실데이터(앱키·망 필요)

⚠️ 투자자문 아님. 시그널만 산출하며 자동 주문하지 않는다. (README의 고지 참조)
"""
from __future__ import annotations

import sys

from indicators.calc import stochastic
from indicators.candles import load_candles, sample_candles
from indicators.notice import NOTICE


def evaluate(candles: list[dict], period: int = 14, smooth_k: int = 3,
             smooth_d: int = 3, high_line: float = 80.0, low_line: float = 20.0) -> dict:
    need = period + smooth_k + smooth_d
    if len(candles) < need:
        return {"state": "NO_SIGNAL", "reason": f"데이터 부족({need}봉 미만)", "detail": {}}
    st = stochastic([c["high"] for c in candles], [c["low"] for c in candles],
                    [c["close"] for c in candles], period, smooth_k, smooth_d)
    k, d = st["k"][-1], st["d"][-1]
    if k is None or d is None:
        return {"state": "NO_SIGNAL", "reason": "고저 범위가 0이라 %K를 낼 수 없습니다", "detail": {}}

    if k >= high_line:
        state, reason = "OVERBOUGHT", f"%K {k:.1f} — 과매수선 {high_line:.0f} 위"
    elif k <= low_line:
        state, reason = "OVERSOLD", f"%K {k:.1f} — 과매도선 {low_line:.0f} 아래"
    else:
        state, reason = "NEUTRAL", f"%K {k:.1f} — {low_line:.0f}~{high_line:.0f} 사이"
    return {"state": state, "reason": reason,
            "detail": {"k": round(k, 2), "d": round(d, 2),
                       "k_above_d": k > d, "close": candles[-1]["close"]}}


def main(argv: list[str]) -> None:
    print(NOTICE)
    if len(argv) > 1:
        candles = load_candles(argv[1])
        src = f"실데이터 {argv[1]} ({len(candles)}봉)"
    else:
        candles = sample_candles()
        src = f"데모 합성데이터 ({len(candles)}봉)"
    r = evaluate(candles)
    print(f"[스토캐스틱] {src}")
    print(f"  지표: {r['state']}  —  {r['reason']}")
    print(f"  상세  : {r['detail']}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        raise SystemExit(f"{e}") from None
