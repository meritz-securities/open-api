"""교육용 단일 종목 ATR 추적 손절(trailing stop) 예제 (투자 추천이 아닙니다).

진입은 단순 추세(종가가 20봉 이동평균 위) 신호로 하고, 청산은 변동성 기반 '추적 손절'로 한다.
보유 중에는 진입 이후의 최고 종가를 계속 갱신하며, 그 최고가에서 ATR(변동성)의 3배만큼
떨어지면 손절한다. 상승 중에는 손절선이 함께 올라가 이익을 보호하고, 하락 시에만 걸린다.

모듈 전역 변수는 한 번의 백테스트 실행 동안 유지되므로 진입 이후 최고가를 여기에 저장한다.
"""
from indicators.calc import sma, atr

_ATR_MULT = 3.0        # 손절 폭 = 최고가 - 3 x ATR
_peak_close = None     # 진입 이후 갱신되는 최고 종가(한 실행 내 유지)


def on_bar(ctx):
    global _peak_close
    history = ctx["history"]
    if len(history) < 21:   # 20봉 평균 + ATR(14) 모두 안정적으로 쓰기 위한 대기
        return {"action": "hold", "reason": "이동평균·ATR 계산에 필요한 봉 대기"}

    closes = [bar["close"] for bar in history]
    highs = [bar["high"] for bar in history]
    lows = [bar["low"] for bar in history]

    trend = sma(closes, 20)[-1]
    atr_now = atr(highs, lows, closes, 14)[-1]
    if trend is None or atr_now is None:
        return {"action": "hold", "reason": "현재 봉 지표 값이 아직 정의되지 않음"}

    close = ctx["bar"]["close"]
    shares = ctx["portfolio"]["shares"]

    if shares == 0:
        # 추세 진입: 종가가 20봉 평균 위
        if close > trend:
            _peak_close = close            # 진입 시점의 최고가 초기화
            return {"action": "buy", "reason": "종가가 20봉 평균 위, 추세 진입"}
        _peak_close = None
        return {"action": "hold", "reason": "추세 진입 조건 미충족"}

    # 보유 중: 최고가 갱신 후 추적 손절선 점검
    if _peak_close is None or close > _peak_close:
        _peak_close = close
    stop = _peak_close - _ATR_MULT * atr_now   # 추적 손절선
    if close < stop:
        _peak_close = None
        return {"action": "sell", "reason": "종가가 ATR 3배 추적 손절선 아래로 하락"}

    return {"action": "hold", "reason": "추적 손절선 위에서 보유 유지"}
