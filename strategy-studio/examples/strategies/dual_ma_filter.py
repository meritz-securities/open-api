"""교육용 단일 종목 이중 이동평균 + RSI/ATR 필터 예제 (투자 추천이 아닙니다).

단순 골든/데드크로스(단기 10봉 vs 장기 30봉)에 두 가지 필터를 더해 신호를 거른다.
  - RSI 필터: 이미 과열(RSI>70)일 때는 뒤늦은 추격 매수를 피한다.
  - ATR 필터: 변동성(ATR/종가)이 지나치게 큰 '난장(whipsaw)' 구간에서는 진입을 보류한다.
필터를 붙이면 교차 신호가 줄지만 신호 품질을 높이려는 의도임을 비교 학습하기 위한 예제다.
"""
from indicators.calc import sma, rsi, atr, crossed_up, crossed_down


def on_bar(ctx):
    history = ctx["history"]
    if len(history) < 31:
        return {"action": "hold", "reason": "장기 30봉 평균·보조지표 계산 대기"}

    closes = [bar["close"] for bar in history]
    highs = [bar["high"] for bar in history]
    lows = [bar["low"] for bar in history]

    fast = sma(closes, 10)
    slow = sma(closes, 30)
    rsi_now = rsi(closes, 14)[-1]
    atr_now = atr(highs, lows, closes, 14)[-1]
    close = ctx["bar"]["close"]
    shares = ctx["portfolio"]["shares"]

    if rsi_now is None or atr_now is None:
        return {"action": "hold", "reason": "현재 봉 필터 지표 값이 아직 정의되지 않음"}

    # 변동성 필터: ATR이 종가의 5%를 넘으면 과도한 변동으로 보고 신규 진입 보류
    too_choppy = (atr_now / close) > 0.05

    # 골든크로스 + 비과열 + 변동성 안정일 때만 매수
    if shares == 0 and crossed_up(fast, slow) and rsi_now < 70 and not too_choppy:
        return {"action": "buy", "reason": "10/30 골든크로스 + RSI<70 + 변동성 안정"}
    # 데드크로스는 필터 없이 즉시 청산(리스크 관리 우선)
    if shares > 0 and crossed_down(fast, slow):
        return {"action": "sell", "reason": "10/30 데드크로스로 추세 종료"}

    return {"action": "hold", "reason": "교차 신호 없음 또는 필터에 의해 보류"}
