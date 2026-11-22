"""교육용 단일 종목 RSI(2) 단기 평균회귀 예제 (투자 추천이 아닙니다).

래리 코너스식 단기 역추세 아이디어다. 긴 추세 필터(종가가 50봉 평균 위, 즉 상승 추세) 안에서만,
짧은 RSI(2)가 10 이하로 급락하는 '눌림'에 매수하고, 반등해 단기 과열(RSI(2) > 70)이 되거나
5봉 평균 위로 올라오면 매도한다. 추세 방향으로만 짧게 되돌림을 노리는 구조다.
"""
from indicators.calc import rsi, sma


def on_bar(ctx):
    closes = [bar["close"] for bar in ctx["history"]]
    trend_period = 50
    if len(closes) < trend_period + 1:
        return {"action": "hold", "reason": "추세 필터(50봉)·RSI(2) 계산 대기"}

    trend = sma(closes, trend_period)[-1]       # 장기 추세 방향
    rsi2 = rsi(closes, 2)[-1]                    # 매우 민감한 단기 RSI
    fast_ma = sma(closes, 5)[-1]                 # 단기 청산 기준선
    if trend is None or rsi2 is None or fast_ma is None:
        return {"action": "hold", "reason": "현재 봉 지표 값이 아직 정의되지 않음"}

    close = ctx["bar"]["close"]
    shares = ctx["portfolio"]["shares"]

    # 상승 추세 안에서의 단기 과매도 매수
    if shares == 0 and close > trend and rsi2 <= 10:
        return {"action": "buy", "reason": "상승 추세(50봉 위) 중 RSI(2)<=10 과매도 눌림"}
    # 단기 과열 또는 단기선 회복 시 청산
    if shares > 0 and (rsi2 >= 70 or close > fast_ma):
        return {"action": "sell", "reason": "RSI(2)>=70 과열 또는 5봉 평균 회복으로 청산"}

    return {"action": "hold", "reason": "진입·청산 조건 미충족"}
