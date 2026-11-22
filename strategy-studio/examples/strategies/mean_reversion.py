"""Educational single-stock mean-reversion example; not an investment recommendation."""


def on_bar(ctx):
    history = ctx["history"]
    if len(history) < 20:
        return {"action": "hold", "reason": "평균 계산에 필요한 20봉 대기"}
    average = sum(bar["close"] for bar in history[-20:]) / 20
    close = ctx["bar"]["close"]
    shares = ctx["portfolio"]["shares"]
    if shares == 0 and close < average * 0.97:
        return {"action": "buy", "reason": "현재 종가가 최근 20봉 평균보다 3% 넘게 낮음"}
    if shares > 0 and close >= average:
        return {"action": "sell", "reason": "현재 종가가 최근 20봉 평균 이상으로 회복"}
    return {"action": "hold", "reason": "설정한 매매 조건에 해당하지 않음"}
