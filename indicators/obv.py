"""OBV — 거래량을 종가 방향으로 누적해 매집·분산을 본다.

표준 정의: 종가가 전일보다 오른 날은 거래량을 더하고, 내린 날은 뺀다.
가격은 제자리인데 OBV 가 올라가면 "조용히 사 모으는 중"으로 읽는 것이 통설이다.
절대값에는 의미가 없다. 여기서는 OBV 자신의 이동평균과 비교해 방향만 판정한다.
데이터: market_candles_days(국내 일봉)의 종가·누적거래량.  계산: indicators.calc.obv

**거래량 필드 주의** — 일봉의 acml_vol 은 그날 하루의 거래량이다. 실시간 시세의
같은 이름 필드는 장중 누적이라 의미가 다르다. 여기서는 일봉만 쓴다.

실행:  python -m indicators.obv            # 데모(네트워크 불필요)
       python -m indicators.obv 005930    # 실데이터(앱키·망 필요)

⚠️ 투자자문 아님. 시그널만 산출하며 자동 주문하지 않는다. (README의 고지 참조)
"""
from __future__ import annotations

import sys

from indicators.calc import obv, sma
from indicators.candles import load_candles, sample_candles
from indicators.notice import NOTICE


def evaluate(candles: list[dict], period: int = 20) -> dict:
    if len(candles) < period + 1:
        return {"state": "NO_SIGNAL", "reason": f"데이터 부족({period + 1}봉 미만)", "detail": {}}
    close = [c["close"] for c in candles]
    line = obv(close, [c["volume"] for c in candles])
    avg = sma(line, period)
    if avg[-1] is None:
        return {"state": "NO_SIGNAL", "reason": "OBV 이동평균 구간이 모자랍니다", "detail": {}}

    # 가격과 OBV 가 서로 다른 방향이면 다이버전스라고 부른다. 방향은 같은
    # 구간(period)의 처음과 끝을 비교해 정한다.
    px_up = close[-1] > close[-1 - period]
    obv_up = line[-1] > line[-1 - period]
    if px_up != obv_up:
        state = "DIVERGENCE"
        reason = (f"{period}봉 동안 가격은 {'상승' if px_up else '하락'}인데 "
                  f"OBV 는 {'상승' if obv_up else '하락'}")
    elif line[-1] > avg[-1]:
        state, reason = "ACCUMULATION", f"OBV 가 {period}일 평균 위 — 같은 방향으로 거래량이 쌓임"
    else:
        state, reason = "DISTRIBUTION", f"OBV 가 {period}일 평균 아래 — 같은 방향으로 거래량이 빠짐"
    return {"state": state, "reason": reason,
            "detail": {"obv": round(line[-1]), f"obv_sma{period}": round(avg[-1]),
                       "price_change_pct": round((close[-1] / close[-1 - period] - 1) * 100, 2),
                       "close": close[-1]}}


def main(argv: list[str]) -> None:
    print(NOTICE)
    if len(argv) > 1:
        candles = load_candles(argv[1])
        src = f"실데이터 {argv[1]} ({len(candles)}봉)"
    else:
        candles = sample_candles()
        src = f"데모 합성데이터 ({len(candles)}봉)"
    r = evaluate(candles)
    print(f"[OBV] {src}")
    print(f"  지표: {r['state']}  —  {r['reason']}")
    print(f"  상세  : {r['detail']}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        raise SystemExit(f"{e}") from None
