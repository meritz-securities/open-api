"""변동성 기준 수량 계산 — 한 번에 잃을 금액을 먼저 정하고 수량을 역산한다.

분할매수(`split_accumulation`)와 대조되는 방식이다. 저쪽은 얼마를 살지를 먼저
정하고 손실 한도가 없지만, 이쪽은 **감수할 손실 금액을 먼저 정하고** 거기서
수량을 거꾸로 구한다.

    감수 금액 = 자본 * 위험비율
    손절 거리 = 배수 * ATR
    수량       = floor(감수 금액 / 손절 거리)

변동성이 큰 종목일수록 손절 거리가 멀어 수량이 줄어든다. 그래서 종목이 달라도
한 번에 잃는 금액이 비슷하게 유지된다는 것이 이 방식의 취지다.

**손절 거리는 사이징에 쓰는 거리이지 손절 주문이 아니다.** 실제로 손절을 걸지
않으면 위험비율 가정이 성립하지 않는다. 걸더라도 갭 하락이면 그 가격에 체결되지
않아 실제 손실이 계산치를 넘는다. 위험비율은 1회 거래 기준이라 동시에 여러
종목을 들면 그만큼 곱해진다.

데이터: market_candles_days(국내 일봉).  계산: indicators.calc.atr

실행:  python -m position_sizing.volatility_sizing                 # 데모
       python -m position_sizing.volatility_sizing 005930          # 실데이터

⚠️ 산출값은 계획이지 주문이 아니다. 투자자문·매매권유가 아니다.
"""
from __future__ import annotations

import math
import sys

from indicators.calc import atr
from indicators.candles import load_candles, sample_candles
from position_sizing.notice import NOTICE, PLAN_NOTICE

ASSUMPTIONS = ("수수료 미반영", "세금 미반영", "슬리피지 미반영",
               "손절이 계산한 가격에 체결된다고 가정", "위험비율은 1회 거래 기준")


def plan(candles: list[dict], equity: float, risk_pct: float = 0.01,
         atr_k: float = 2.0, period: int = 14,
         max_exposure_pct: float = 0.20) -> dict:
    """감수할 손실 금액에서 수량을 역산한다. 주문하지 않는다."""
    if equity <= 0 or risk_pct <= 0 or atr_k <= 0 or period <= 0:
        return {"state": "NO_SIGNAL", "reason": "자본·위험비율·배수가 유효하지 않습니다",
                "detail": {}}
    if len(candles) < period + 2:
        return {"state": "NO_SIGNAL", "reason": f"데이터 부족({period + 2}봉 미만)", "detail": {}}

    a = atr([c["high"] for c in candles], [c["low"] for c in candles],
            [c["close"] for c in candles], period)[-1]
    price = candles[-1]["close"]
    if not a or price <= 0:
        # ATR 0 은 보합·거래정지 구간이다. 나누면 수량이 무한대가 된다.
        return {"state": "NO_SIGNAL",
                "reason": "ATR 이 0 입니다. 변동이 없는 구간에서는 수량을 낼 수 없습니다",
                "detail": {}}

    risk_amount = equity * risk_pct
    stop_distance = atr_k * a
    qty = math.floor(risk_amount / stop_distance)
    cap_qty = math.floor(equity * max_exposure_pct / price)

    if qty == 0:
        state = "SIZE_ZERO"
        reason = (f"감수 금액 {risk_amount:,.0f} 이 손절 거리 {stop_distance:,.0f} 보다 작아 "
                  f"1주도 나오지 않습니다")
    elif qty > cap_qty:
        qty, state = cap_qty, "SIZE_CAPPED"
        reason = (f"익스포저 상한 {max_exposure_pct:.0%} 에 걸려 {qty:,}주로 줄였습니다. "
                  f"이 수량에서는 위험비율 가정이 성립하지 않습니다")
    else:
        state = "SIZE_COMPUTED"
        reason = f"손절 거리 {stop_distance:,.0f} 기준 {qty:,}주"

    exposure = qty * price
    return {
        "state": state, "reason": reason,
        "detail": {
            "price": price, "atr": round(a, 1),
            "atr_pct": round(a / price * 100, 2),
            "risk_amount": round(risk_amount),
            "stop_distance": round(stop_distance),
            "stop_price": round(price - stop_distance),
            "qty": qty, "exposure": round(exposure),
            "exposure_pct": round(exposure / equity * 100, 1),
            "actual_risk": round(qty * stop_distance),
            "worst_case": round(exposure),      # 손절이 듣지 않으면 익스포저 전액
            "assumptions": list(ASSUMPTIONS),
        },
    }


def main(argv: list[str]) -> None:
    print(NOTICE)
    print(PLAN_NOTICE)
    equity = 100_000_000.0
    if len(argv) > 1:
        candles = load_candles(argv[1])
        src = f"실데이터 {argv[1]} ({len(candles)}봉)"
    else:
        candles = sample_candles()
        src = f"데모 합성데이터 ({len(candles)}봉)"
    r = plan(candles, equity)
    d = r["detail"]
    print(f"\n[변동성 기준 수량] 자본 {equity:,.0f} · {src}")
    print(f"  지표: {r['state']}  —  {r['reason']}")
    if d:
        print(f"  ATR {d['atr']:,.0f} ({d['atr_pct']}%) · 손절 거리 {d['stop_distance']:,.0f} "
              f"· 손절가 {d['stop_price']:,.0f}")
        print(f"  수량 {d['qty']:,} · 익스포저 {d['exposure']:,.0f} ({d['exposure_pct']}%) "
              f"· 계산상 위험 {d['actual_risk']:,.0f}")
        print(f"  손절이 듣지 않으면 최대 손실 {d['worst_case']:,.0f}")
    print("\n  * 손절 거리는 사이징용 거리이며 손절 주문이 아닙니다.")
    print("  * 갭 하락이면 손절가에 체결되지 않아 실제 손실이 계산치를 넘습니다.")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:
        raise SystemExit(f"{e}") from None
