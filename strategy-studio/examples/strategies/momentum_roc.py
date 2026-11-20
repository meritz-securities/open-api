"""교육용 단일 종목 모멘텀(ROC) 타이밍 예제 (투자 추천이 아닙니다).

여러 종목을 순환매하는 모멘텀 로테이션의 '단일 종목' 축소판이다.
변화율 ROC = (현재 종가 / N봉 전 종가 - 1) x 100 으로 상승 탄력을 측정한다.
ROC가 양(+)의 임계선을 넘고 장기 추세(100봉 평균) 위에 있으면 '보유할 가치가 있다'고 보고 매수하고,
ROC가 0 아래로 꺼지면 모멘텀이 사라졌다고 보고 비중을 비운다(현금화).
ROC는 표준 라이브러리 지표에 없어 종가만으로 직접 계산한다.
"""
from indicators.calc import sma


def on_bar(ctx):
    closes = [bar["close"] for bar in ctx["history"]]
    lookback = 20          # ROC 측정 구간
    trend_period = 100     # 장기 추세 필터
    if len(closes) < trend_period + 1:
        return {"action": "hold", "reason": "ROC·장기 추세 계산에 필요한 봉 대기"}

    past = closes[-lookback - 1]
    if past == 0:
        return {"action": "hold", "reason": "과거 종가가 0이라 ROC 계산 불가"}
    roc = (closes[-1] / past - 1.0) * 100.0     # 20봉 변화율(%)

    trend = sma(closes, trend_period)[-1]
    if trend is None:
        return {"action": "hold", "reason": "현재 봉 추세 값이 아직 정의되지 않음"}

    close = ctx["bar"]["close"]
    shares = ctx["portfolio"]["shares"]

    # 강한 양의 모멘텀 + 장기 상승 추세에서만 편입
    if shares == 0 and roc > 5.0 and close > trend:
        return {"action": "buy", "reason": "20봉 ROC>5% 이고 100봉 평균 위(양의 모멘텀 편입)"}
    # 모멘텀 소멸 시 현금화
    if shares > 0 and roc < 0.0:
        return {"action": "sell", "reason": "20봉 ROC가 0 아래로 하락(모멘텀 소멸, 현금화)"}

    return {"action": "hold", "reason": "모멘텀·추세 조건 미충족"}
