"""교육용 단일 종목 돈치안 채널 돌파(터틀 방식) 예제 (투자 추천이 아닙니다).

유명한 '터틀 트레이딩'의 뼈대다. 직전 20봉 최고가를 새로 돌파하면 추세 시작으로 보고 매수하고,
직전 10봉 최저가를 깨고 내려가면 추세 종료로 보고 전량 매도한다.
돈치안 채널은 '당일을 제외한' 과거 구간의 고·저라서, 당일 고가가 상단을 넘으면 신고가 돌파로 잡힌다.
수량을 생략하므로 매수는 가용 현금 전액, 매도는 보유 전량으로 체결된다.
"""
from indicators.calc import donchian


def on_bar(ctx):
    history = ctx["history"]
    entry_period = 20   # 진입: 20봉 신고가 돌파
    exit_period = 10    # 청산: 10봉 신저가 이탈
    # donchian은 i > period 구간부터 값이 생긴다. 넉넉히 entry_period+1 봉 이상 대기.
    if len(history) <= entry_period:
        return {"action": "hold", "reason": "돈치안 채널 계산에 필요한 20봉 대기"}

    highs = [bar["high"] for bar in history]
    lows = [bar["low"] for bar in history]
    close = ctx["bar"]["close"]
    shares = ctx["portfolio"]["shares"]

    upper = donchian(highs, lows, entry_period)["upper"][-1]   # 직전 20봉 최고가
    lower = donchian(highs, lows, exit_period)["lower"][-1]    # 직전 10봉 최저가

    # 20봉 신고가 돌파 매수
    if shares == 0 and upper is not None and close > upper:
        return {"action": "buy", "reason": "종가가 직전 20봉 최고가를 돌파(터틀 진입)"}
    # 10봉 신저가 이탈 청산
    if shares > 0 and lower is not None and close < lower:
        return {"action": "sell", "reason": "종가가 직전 10봉 최저가를 이탈(터틀 청산)"}

    return {"action": "hold", "reason": "채널 내부, 돌파·이탈 없음"}
