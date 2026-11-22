"""골든크로스/데드크로스 — 단기·장기 이동평균의 교차.

표준 정의: 단기 이동평균이 장기 이동평균을 상향 돌파하면 골든크로스,
하향 돌파하면 데드크로스라고 부른다. 교차 발생 여부만 판정한다.
데이터: market_candles_days(국내 일봉)의 종가.  계산: indicators.calc.sma

실행:  python -m indicators.golden_cross            # 데모(네트워크 불필요)
       python -m indicators.golden_cross 005930    # 실데이터(앱키·망 필요)

⚠️ 투자자문 아님. 시그널만 산출하며 자동 주문하지 않는다. (README의 고지 참조)
"""
from __future__ import annotations

import sys

from indicators.calc import crossed_down, crossed_up, sma
from indicators.candles import closes, load_candles, sample_candles
from indicators.notice import NOTICE


def evaluate(candles: list[dict], short: int = 5, long: int = 20) -> dict:
    px = closes(candles)
    if len(px) < long + 1:
        return {"state": "NO_SIGNAL", "reason": f"데이터 부족(장기 {long}일 미만)", "detail": {}}
    s, l = sma(px, short), sma(px, long)
    if crossed_up(s, l):
        state, reason = "CROSS_UP", f"상향 교차: {short}일선이 {long}일선을 상향 돌파"
    elif crossed_down(s, l):
        state, reason = "CROSS_DOWN", f"하향 교차: {short}일선이 {long}일선을 하향 돌파"
    else:
        above = s[-1] is not None and l[-1] is not None and s[-1] > l[-1]
        state = "MIXED"
        reason = f"교차 없음 (단기선이 장기선 {'위' if above else '아래'})"
    return {"state": state, "reason": reason,
            "detail": {f"sma{short}": round(s[-1], 1) if s[-1] is not None else None,
                       f"sma{long}": round(l[-1], 1) if l[-1] is not None else None,
                       "close": px[-1]}}


def main(argv: list[str]) -> None:
    print(NOTICE)
    if len(argv) > 1:
        candles = load_candles(argv[1])
        src = f"실데이터 {argv[1]} ({len(candles)}봉)"
    else:
        candles = sample_candles()
        src = f"데모 합성데이터 ({len(candles)}봉)"
    result = evaluate(candles)
    print(f"[골든크로스] {src}")
    print(f"  지표: {result['state']}  —  {result['reason']}")
    print(f"  상세  : {result['detail']}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        # 앱키가 그 서버의 것이 아니면 여기로 온다. 메시지에 원인이
        # 들어 있으므로 트레이스백 대신 한 줄로 끝낸다.
        raise SystemExit(f"{e}") from None
