"""교육용 단일 종목 볼린저밴드 평균회귀 예제 (투자 추천이 아닙니다).

가격이 하단 밴드(평균 - 2σ) 아래로 과하게 벌어지면 되돌림을 기대해 매수하고,
중심선(20봉 이동평균)까지 회복하면 매도한다. 추세장보다 횡보장에서 쓰는 역추세 아이디어다.
수량을 생략하므로 매수는 가용 현금 전액, 매도는 보유 전량으로 체결된다.
"""
from indicators.calc import bollinger


def on_bar(ctx):
    closes = [bar["close"] for bar in ctx["history"]]
    period = 20
    # 밴드(이동평균+표준편차) 계산에 20봉이 필요하다.
    if len(closes) < period:
        return {"action": "hold", "reason": "볼린저밴드 계산에 필요한 20봉 대기"}

    band = bollinger(closes, period, 2.0)   # {'mid', 'upper', 'lower'} 각 시점 리스트
    mid = band["mid"][-1]
    lower = band["lower"][-1]
    if mid is None or lower is None:
        return {"action": "hold", "reason": "현재 봉 밴드 값이 아직 정의되지 않음"}

    close = ctx["bar"]["close"]
    shares = ctx["portfolio"]["shares"]

    # 하단 밴드 이탈: 평균 대비 과매도로 보고 되돌림 매수
    if shares == 0 and close < lower:
        return {"action": "buy", "reason": "종가가 하단 밴드(-2σ) 아래로 이탈(과매도 되돌림 기대)"}
    # 중심선 복귀: 평균 회귀 목표 달성으로 보고 청산
    if shares > 0 and close >= mid:
        return {"action": "sell", "reason": "종가가 중심선(20봉 평균)까지 회복"}

    return {"action": "hold", "reason": "밴드 내부에 위치, 조건 미충족"}
