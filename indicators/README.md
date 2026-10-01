# 기술지표 계산 예제

> **투자권유·투자자문·매매 추천이 아니며, 투자광고가 아닙니다.**
> 공개된 표준 기술적 지표의 계산 방법을 보여 주는 개발 예제입니다.
> 메리츠증권은 이 코드가 산출하는 어떠한 값도 투자 판단의 근거로 제공하지 않습니다.
> 특정 종목의 매매를 권유하지 않으며, 산출값을 주문에 연결하지 마십시오.

메리츠증권 Open API 로 받은 시세로 기술지표를 계산하는 Python 예제입니다.
**지표 상태만 산출하며 매수·매도를 지시하지 않습니다.** 주문을 전송하지도 않습니다.

## 계산 모듈

| 파일 | 계산하는 것 |
|---|---|
| `calc.py` | SMA · EMA · RSI · 볼린저 밴드 · MACD · True Range · ATR · 스토캐스틱 · OBV · 돈치안 |
| `candles.py` | 일봉 로더 · 데모 데이터 |

## 지표 — 일봉으로 계산

| 파일 | 보는 것 | 산출 상태 |
|---|---|---|
| `golden_cross.py` | 단기·장기 이동평균의 교차 | `CROSS_UP` `CROSS_DOWN` `MIXED` |
| `macd.py` | MACD 선과 시그널선의 교차 | `CROSS_UP` `CROSS_DOWN` `MIXED` |
| `rsi_reversal.py` | RSI 가 상·하단 기준을 벗어났는지 | `OVERBOUGHT` `OVERSOLD` `NEUTRAL` |
| `stochastic.py` | 기간 고저 범위에서 종가의 위치 | `OVERBOUGHT` `OVERSOLD` `NEUTRAL` |
| `bollinger.py` | 종가가 볼린저 밴드를 벗어났는지 | `ABOVE_UPPER` `BELOW_LOWER` `INSIDE` |
| `price_channel.py` | 직전 n봉의 최고·최저 돌파 | `BREAK_UPPER` `BREAK_LOWER` `INSIDE` |
| `atr.py` | 변동성의 크기 | `VOLATILITY_HIGH` `VOLATILITY_LOW` `VOLATILITY_NORMAL` |
| `obv.py` | 거래량을 종가 방향으로 누적 | `ACCUMULATION` `DISTRIBUTION` `DIVERGENCE` |
| `supply_demand.py` | 외국인·기관 순매수 합의 방향 | `NET_BUY` `NET_SELL` `MIXED` |

## 지표 — 서버가 주는 값을 읽음

일봉을 모으지 않고 조회 한 번으로 끝납니다.

| 파일 | 보는 것 | 산출 상태 |
|---|---|---|
| `server_levels.py` | 서버가 계산해 주는 피벗·지지·저항 | `ABOVE_R2` `BETWEEN_R1_R2` `ABOVE_PIVOT` `BELOW_PIVOT` `BETWEEN_S1_S2` `BELOW_S2` |
| `orderbook_imbalance.py` | 호가 총잔량의 매수·매도 쏠림 | `BID_HEAVY` `ASK_HEAVY` `BALANCED` |

## 집계

| 파일 | 하는 일 |
|---|---|
| `backtest.py` | 과거 구간에서 각 지표가 어떤 상태였는지 집계 |

**사건형과 상태형을 섞어 세지 않습니다.** 교차(골든크로스·MACD)는 그 봉에서만
참이므로 **발생 횟수**를, 나머지는 조건이 유지되는 동안 계속 참이므로 **머문 봉 수**를
셉니다. 두 표의 숫자는 단위가 다르므로 가로로 비교하면 안 됩니다.

`backtest.py`는 **수익률을 계산하지 않습니다.** 매매 성과를 추정하려면 수수료·세금·
슬리피지·체결 지연·호가 단위·유동성을 모두 반영해야 하고, 그것은 이 예제의 범위가
아닙니다.

## 산출값의 뜻

`NO_SIGNAL` 은 **데이터가 모자라 판정하지 못했다**는 뜻으로만 씁니다. 중립은
판정한 결과이므로 `MIXED`·`NEUTRAL`·`INSIDE` 처럼 제 이름을 가집니다. 데이터
유효성을 거르실 때 `NO_SIGNAL` 하나만 보시면 됩니다.

모든 지표는 `state` 하나를 돌려줍니다. **"사라/팔라"가 아니라 "지표가 어느 쪽에
걸렸다"는 사실만 나타냅니다.** 상태 이름에 `BUY`·`SELL`·`HOLD` 를 쓰지 않으며,
그 규칙은 테스트(`tests/test_indicators.py`)로 강제됩니다.
데이터가 모자라 판정할 수 없으면 `NO_SIGNAL` 입니다.

## 실행

앱키 없이 데모 데이터로 확인할 수 있습니다.

```bash
python -m indicators.golden_cross
python -m indicators.backtest
```

실데이터로 계산하려면 앱키를 설정하고 종목코드를 넘깁니다.

```bash
export MERITZ_APP_KEY=발급받은_앱키
export MERITZ_APP_SECRET=발급받은_시크릿

python -m indicators.stochastic 005930
python -m indicators.server_levels 005930
```

실행하면 출력 첫 줄에 고지가 함께 나옵니다.

## 데이터

`market_candles_days`(국내 일봉)의 OHLCV 와 투자자별 순매수를 씁니다.
**한 번 호출에 오는 것이 전부입니다**(삼성전자 기준 40봉) — 이 API 는 연속조회를
지원하지 않아 더 과거를 받을 수 없습니다. 200일선처럼 긴 지표는 계산되지 않고,
`backtest.py`의 빈도 집계도 실데이터로는 구간이 남지 않아 데모 데이터로만 됩니다.

당일 봉은 장중에도 장 종료 후에도 생성되지 않습니다. 목록의 마지막은 언제나
직전 영업일입니다. 당일 값이 필요하면 현재가 조회를 쓰십시오.

## 테스트

```bash
uv run pytest tests/test_indicators.py -q
```

계산식을 손계산 기대값과 대조하고, 상태 이름에 매매 지시어가 섞이지 않는지 검사합니다.

## 한계

- **기본 호출 대상은 운영 서버입니다.** 개발 서버로 붙으려면 `MERITZ_BASE_URL` 과
  `MERITZ_WS_URL` 을 둘 다 지정하십시오.
- 지정한 종목만 계산합니다. 종목 탐색·추천 기능은 없습니다.
- 파라미터(기간·기준선)는 널리 쓰이는 기본값일 뿐 최적값이 아닙니다.
- 산출값을 주문에 연결하기 전에 데이터·종목·수량·가격을 직접 검토하십시오.
