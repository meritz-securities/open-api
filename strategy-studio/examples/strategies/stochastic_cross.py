"""교육용 단일 종목 스토캐스틱(Slow) 교차 예제 (투자 추천이 아닙니다).

스토캐스틱은 최근 고·저 범위에서 종가의 상대적 위치(0~100)를 본다.
과매도 구간(20 이하)에서 %K가 %D를 상향 돌파하면 반등 신호로 보고 매수하고,
과매수 구간(80 이상)에서 %K가 %D를 하향 돌파하면 조정 신호로 보고 매도한다.
오실레이터 교차에 '구간 조건'을 더해 중립 구간의 잦은 신호를 걸러내는 구조다.
"""
from indicators.calc import stochastic, crossed_up, crossed_down


def on_bar(ctx):
    history = ctx["history"]
    # 슬로우 스토캐스틱(14,3,3)은 약 18봉 이상부터 %K/%D가 안정적으로 나온다.
    if len(history) < 20:
        return {"action": "hold", "reason": "스토캐스틱(14,3,3) 계산에 필요한 봉 대기"}

    highs = [bar["high"] for bar in history]
    lows = [bar["low"] for bar in history]
    closes = [bar["close"] for bar in history]

    st = stochastic(highs, lows, closes, 14, 3, 3)   # {'fast_k', 'k', 'd'}
    k = st["k"]
    d = st["d"]
    if k[-1] is None or d[-1] is None:
        return {"action": "hold", "reason": "현재 봉 %K/%D 값이 아직 정의되지 않음"}

    shares = ctx["portfolio"]["shares"]

    # 과매도 구간에서의 상향 교차 매수
    if shares == 0 and k[-1] < 20 and crossed_up(k, d):
        return {"action": "buy", "reason": "과매도(%K<20) 구간에서 %K가 %D를 상향 돌파"}
    # 과매수 구간에서의 하향 교차 매도
    if shares > 0 and k[-1] > 80 and crossed_down(k, d):
        return {"action": "sell", "reason": "과매수(%K>80) 구간에서 %K가 %D를 하향 돌파"}

    return {"action": "hold", "reason": "교차·구간 조건 미충족"}
