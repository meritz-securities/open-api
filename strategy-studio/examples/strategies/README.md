# 전략 예제 모음

모두 **교육용**이며 투자 추천이 아닙니다. 각 파일은 단일 UTF-8 `.py`로
`def on_bar(ctx):` 하나만 정의하는 [전략 계약 v1](../../docs/strategy-contract.md)을 따릅니다.
표준 라이브러리와 호스트가 주입하는 `indicators` 모듈만 사용하며, 파일·네트워크 접근은 없습니다.

지표는 `from indicators.calc import sma, ema, rsi, macd, bollinger, atr, stochastic, donchian, crossed_up, crossed_down`
형태로 가져옵니다. 모두 단일 종목·롱 온리·일봉 기준이며 한 봉에 한 주문만 반환합니다
(수량 생략 시 매수는 가용 현금 전액, 매도는 보유 전량).

| 파일 | 아이디어 |
|---|---|
| [hold.py](hold.py) | 관측만 수행(계약 학습용) |
| [buy_once.py](buy_once.py) | 최초 1회 매수 후 보유 |
| [mean_reversion.py](mean_reversion.py) | 20봉 평균 대비 이격 매수 |
| [macd_cross.py](macd_cross.py) | MACD가 시그널선을 교차 |
| [bollinger_reversion.py](bollinger_reversion.py) | 볼린저 하단 이탈 되돌림(역추세) |
| [bollinger_breakout.py](bollinger_breakout.py) | 볼린저 상단 돌파(변동성 추세) |
| [donchian_breakout.py](donchian_breakout.py) | 20봉 신고가 돌파(터틀 방식) |
| [atr_trailing_stop.py](atr_trailing_stop.py) | ATR 기반 추적 손절 청산 |
| [rsi2_reversion.py](rsi2_reversion.py) | 추세 필터 안의 RSI(2) 단기 과매도 |
| [dual_ma_filter.py](dual_ma_filter.py) | 10/30 이중 이동평균 + RSI·ATR 필터 |
| [stochastic_cross.py](stochastic_cross.py) | 스토캐스틱 %K·%D 과매도/과매수 교차 |
| [momentum_roc.py](momentum_roc.py) | ROC 모멘텀 + 장기 추세 편입 |
| [keltner_breakout.py](keltner_breakout.py) | 켈트너 채널(EMA±ATR) 상단 돌파 |

> 거래량(volume) 기반 지표(OBV 등)는 백테스트 봉(`ctx["bar"]`)에 거래량 필드가 없어 제외했습니다.
