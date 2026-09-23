"""수급 관측 (외국인·기관 순매수) — 메리츠 데이터 특성을 활용.

market_candles_days(국내 일봉) 응답에는 종가뿐 아니라 **외국인 순매수수량·기관 순매수거래량**이
함께 온다. 별도 수급 데이터 없이도 "기관·외국인이 최근 며칠 순매수 중인가"를 판단할 수 있다.

로직(표준적 수급 해석):
  - 최근 window 일간 (외국인+기관) 순매수 합이 양(+)이고, 당일 종가가 상승 → NET_BUY
  - 합이 음(-)이고 종가 하락 → NET_SELL
  - 그 외 → MIXED

데이터: market_candles_days.  실행: python -m indicators.supply_demand [종목코드]

⚠️ 투자자문 아님. 시그널만 산출한다.
"""
from __future__ import annotations

import sys

from indicators.candles import load_candles, sample_candles
from indicators.notice import NOTICE


def evaluate(candles: list[dict], window: int = 5) -> dict:
    if len(candles) < window + 1:
        return {"state": "NO_SIGNAL", "reason": f"데이터 부족(최근 {window}일 미만)", "detail": {}}
    recent = candles[-window:]
    frgn = sum(c.get("frgn_ntby", 0.0) for c in recent)
    orgn = sum(c.get("orgn_ntby", 0.0) for c in recent)
    net = frgn + orgn
    up = candles[-1]["close"] > candles[-2]["close"]
    if net > 0 and up:
        state = "NET_BUY"
        reason = f"순매수 우위: 최근 {window}일 외국인+기관 순매수 +{net:,.0f} & 종가 상승"
    elif net < 0 and not up:
        state = "NET_SELL"
        reason = f"순매도 우위: 최근 {window}일 외국인+기관 순매도 {net:,.0f} & 종가 하락"
    else:
        state = "MIXED"
        reason = f"혼조: 순매수합 {net:,.0f}, 종가 {'상승' if up else '하락'}"
    return {"state": state, "reason": reason,
            "detail": {"frgn_ntby_sum": frgn, "orgn_ntby_sum": orgn, "net": net}}


def main(argv: list[str]) -> None:
    print(NOTICE)
    if len(argv) > 1:
        candles = load_candles(argv[1])
        src = f"실데이터 {argv[1]} ({len(candles)}봉)"
    else:
        candles = sample_candles()
        src = f"데모 합성데이터 ({len(candles)}봉)"
    result = evaluate(candles)
    print(f"[수급(외국인·기관)] {src}")
    print(f"  지표: {result['state']}  —  {result['reason']}")
    print(f"  상세  : {result['detail']}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        # 앱키가 그 서버의 것이 아니면 여기로 온다. 메시지에 원인이
        # 들어 있으므로 트레이스백 대신 한 줄로 끝낸다.
        raise SystemExit(f"{e}") from None
