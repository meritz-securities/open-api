"""교육용 단일 종목 MACD 교차 예제 (투자 추천이 아닙니다).

MACD 선(12,26)이 시그널 선(9)을 상향 돌파하면 추세가 위로 돌았다고 보고 매수하고,
하향 돌파하면 추세가 꺾였다고 보고 전량 매도하는 가장 기본적인 추세추종 뼈대다.
수량을 생략하므로 매수는 가용 현금 전액, 매도는 보유 전량으로 체결된다.
"""
from indicators.calc import macd, crossed_up, crossed_down


def on_bar(ctx):
    closes = [bar["close"] for bar in ctx["history"]]
    # MACD 계산에는 최소 slow(26)+signal(9) 구간이 필요하다. 그 전에는 관망.
    if len(closes) < 35:
        return {"action": "hold", "reason": "MACD·시그널 계산에 필요한 35봉 대기"}

    lines = macd(closes)              # {'macd': [...], 'signal': [...], 'hist': [...]}
    macd_line = lines["macd"]
    signal_line = lines["signal"]
    shares = ctx["portfolio"]["shares"]

    # 골든크로스: MACD가 시그널을 아래→위로 통과 (crossed_up은 직전/현재 두 점만 본다)
    if shares == 0 and crossed_up(macd_line, signal_line):
        return {"action": "buy", "reason": "MACD가 시그널선을 상향 돌파(모멘텀 전환)"}
    # 데드크로스: MACD가 시그널을 위→아래로 통과
    if shares > 0 and crossed_down(macd_line, signal_line):
        return {"action": "sell", "reason": "MACD가 시그널선을 하향 돌파(모멘텀 소멸)"}

    return {"action": "hold", "reason": "교차 신호 없음"}
