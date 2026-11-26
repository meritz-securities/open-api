"""MACD 교차 판정.

표준 정의: MACD(12,26,9). MACD선 = EMA12 − EMA26, 시그널선 = MACD선의 EMA9.
두 선의 교차 발생 여부와 히스토그램 부호만 판정한다.
데이터: market_candles_days 종가.  계산: indicators.calc.macd

실행:  python -m indicators.macd            # 데모
       python -m indicators.macd 005930     # 실데이터

⚠️ 투자자문 아님. 시그널만 산출한다.
"""
from __future__ import annotations

import sys

from indicators.calc import crossed_down, crossed_up, macd
from indicators.candles import closes, load_candles, sample_candles
from indicators.notice import NOTICE


def evaluate(candles: list[dict], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    px = closes(candles)
    if len(px) < slow + signal:
        return {"state": "NO_SIGNAL", "reason": "데이터 부족(MACD 계산 불가)", "detail": {}}
    m = macd(px, fast, slow, signal)
    macd_line, sig_line, hist = m["macd"], m["signal"], m["hist"]
    if crossed_up(macd_line, sig_line):
        out, reason = "CROSS_UP", "MACD선이 시그널선을 상향 돌파"
    elif crossed_down(macd_line, sig_line):
        out, reason = "CROSS_DOWN", "MACD선이 시그널선을 하향 돌파"
    else:
        pos = "양(+)" if (hist[-1] or 0) > 0 else "음(-)"
        out, reason = "MIXED", f"교차 없음 (히스토그램 {pos})"
    return {"state": out, "reason": reason,
            "detail": {"macd": round(macd_line[-1], 2) if macd_line[-1] is not None else None,
                       "signal_line": round(sig_line[-1], 2) if sig_line[-1] is not None else None,
                       "hist": round(hist[-1], 2) if hist[-1] is not None else None}}


def main(argv: list[str]) -> None:
    print(NOTICE)
    if len(argv) > 1:
        candles = load_candles(argv[1])
        src = f"실데이터 {argv[1]} ({len(candles)}봉)"
    else:
        candles = sample_candles()
        src = f"데모 합성데이터 ({len(candles)}봉)"
    result = evaluate(candles)
    print(f"[MACD] {src}")
    print(f"  지표: {result['state']}  —  {result['reason']}")
    print(f"  상세  : {result['detail']}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        # 앱키가 그 서버의 것이 아니면 여기로 온다. 메시지에 원인이
        # 들어 있으므로 트레이스백 대신 한 줄로 끝낸다.
        raise SystemExit(f"{e}") from None
