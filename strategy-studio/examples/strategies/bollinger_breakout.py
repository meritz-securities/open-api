"""교육용 단일 종목 볼린저밴드 변동성 돌파 예제 (투자 추천이 아닙니다).

같은 볼린저밴드라도 '추세' 관점에서는 반대로 쓴다. 종가가 상단 밴드(평균 + 2σ)를
뚫고 올라가면 변동성 확장과 함께 추세가 시작됐다고 보고 매수하고,
중심선(20봉 평균) 아래로 되돌아오면 추세가 식었다고 보고 매도한다.
평균회귀 예제(bollinger_reversion)와 신호 방향이 정반대임을 비교해 학습하기 위한 예제다.
"""
from indicators.calc import bollinger


def on_bar(ctx):
    closes = [bar["close"] for bar in ctx["history"]]
    period = 20
    if len(closes) < period:
        return {"action": "hold", "reason": "볼린저밴드 계산에 필요한 20봉 대기"}

    band = bollinger(closes, period, 2.0)
    mid = band["mid"][-1]
    upper = band["upper"][-1]
    if mid is None or upper is None:
        return {"action": "hold", "reason": "현재 봉 밴드 값이 아직 정의되지 않음"}

    close = ctx["bar"]["close"]
    shares = ctx["portfolio"]["shares"]

    # 상단 밴드 돌파: 변동성 확장 + 상승 추세 시작으로 해석해 진입
    if shares == 0 and close > upper:
        return {"action": "buy", "reason": "종가가 상단 밴드(+2σ)를 돌파(변동성 확장 추세 진입)"}
    # 중심선 이탈: 추세 약화로 보고 청산
    if shares > 0 and close < mid:
        return {"action": "sell", "reason": "종가가 중심선(20봉 평균) 아래로 복귀(추세 약화)"}

    return {"action": "hold", "reason": "돌파·이탈 신호 없음"}
