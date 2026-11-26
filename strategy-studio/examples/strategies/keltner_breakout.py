"""교육용 단일 종목 켈트너 채널 돌파 예제 (투자 추천이 아닙니다).

켈트너 채널은 중심선 = EMA(20), 상·하단 = 중심선 ± 배수 x ATR 로 만든 변동성 채널이다.
볼린저밴드가 표준편차를 쓰는 데 비해 켈트너는 ATR(실제 변동폭)을 써서 채널 폭이 더 매끈하다.
종가가 상단 채널을 돌파하면 추세 시작으로 보고 매수하고, 중심선(EMA) 아래로 복귀하면 청산한다.
라이브러리에 켈트너 함수는 없으므로 EMA와 ATR을 조합해 직접 구성한다.
"""
from indicators.calc import ema, atr


def on_bar(ctx):
    history = ctx["history"]
    period = 20
    mult = 2.0     # 채널 폭 = 2 x ATR
    if len(history) < period + 15:   # EMA(20) + ATR(14) 안정 구간 확보
        return {"action": "hold", "reason": "EMA·ATR 기반 켈트너 채널 계산 대기"}

    closes = [bar["close"] for bar in history]
    highs = [bar["high"] for bar in history]
    lows = [bar["low"] for bar in history]

    mid = ema(closes, period)[-1]                 # 중심선
    atr_now = atr(highs, lows, closes, 14)[-1]    # 채널 폭의 재료
    if mid is None or atr_now is None:
        return {"action": "hold", "reason": "현재 봉 EMA/ATR 값이 아직 정의되지 않음"}

    upper = mid + mult * atr_now                  # 상단 채널
    close = ctx["bar"]["close"]
    shares = ctx["portfolio"]["shares"]

    # 상단 채널 돌파 매수
    if shares == 0 and close > upper:
        return {"action": "buy", "reason": "종가가 켈트너 상단(EMA+2·ATR)을 돌파"}
    # 중심선 복귀 청산
    if shares > 0 and close < mid:
        return {"action": "sell", "reason": "종가가 켈트너 중심선(EMA20) 아래로 복귀"}

    return {"action": "hold", "reason": "채널 내부, 돌파·이탈 없음"}
