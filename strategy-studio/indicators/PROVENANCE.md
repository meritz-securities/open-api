# Source provenance

`calc.py` 의 기존 지표(SMA·EMA·RSI·볼린저밴드·MACD·ATR·스토캐스틱·OBV·돈치안)는
Meritz Open API examples `indicators/calc.py`, repository commit
c2510ae8c896bada1641c49d39e1c69515bbe479 (local checked source, 2026-11-12), MIT license
에서 그대로 재사용했다. The existing root LICENSE retains the copyright notice. SDK and DSL
use these same formulas. RSI with zero average loss returns 100, including flat input; warmup
remains None. This is an explicit formula convention, not an investment signal recommendation.

## 확장 지표 (2026-11-16 추가)

아래 지표는 기존 `calc.py` 스타일·시그니처를 따라 **순수 파이썬 표준 라이브러리만으로** 새로
구현했다(서드파티 의존성 없음). 모두 공용(퍼블릭) 기술적 분석 정의를 따르며 특정 회사 소유가
아니다. 입력은 과거→현재 순 리스트, 반환은 각 시점 값 리스트(또는 dict of 리스트)이고 워밍업·
정의불가 구간은 기존 함수와 동일하게 `None` 이다. NaN 은 사용하지 않는다.

지표마다 표준 공식 정의를 적는다. 수치 거동은 `tests/test_indicators_expanded.py` 에서 손계산
값·수학적 성질·경계 조건으로 검증한다. 상용 라이브러리(ta-lib 등) 대비 비트 단위 일치를 보장하지
않는다(평활 시딩·엣지 처리 관례 차이).

### 가격(종가) 전용
- **wma** — Weighted MA. 가중치 1..period(최신=period), 분모 삼각수 period·(period+1)/2.
- **hma** — Hull MA. `WMA(2·WMA(n/2) − WMA(n), √n)` (Alan Hull). 구현상 `√n` 은
  `int(n ** 0.5)`(버림, 최소 1)로 잡는다 — 정수 창 길이가 필요하기 때문이며, 상용 라이브러리의
  반올림 변형과 1 봉 차이가 날 수 있다(값 변경 아님, 관례 명시).
- **dema** — Double EMA. `2·EMA − EMA(EMA)` (Patrick Mulloy, 1994).
- **tema** — Triple EMA. `3·EMA − 3·EMA² + EMA³` (Patrick Mulloy, 1994).
- **trima** — Triangular MA. SMA 를 두 번 적용(짝수 n: n/2 & n/2+1, 홀수 n: (n+1)/2 두 번).
- **kama** — Kaufman Adaptive MA. 효율비 ER=|Δperiod|/Σ|Δ1| 로 평활상수 SC 가변(fast=2,slow=30).
- **zlema** — Zero-Lag EMA. `EMA(종가 + (종가 − 종가[lag]))`, lag=(period−1)//2 (Ehlers).
- **t3** — Tillson T3. EMA 6회 사슬의 가중합, 계수는 vfactor(기본 0.7)로 결정.
- **stdev** — 이동 표준편차(모집단, 분모 period). 볼린저밴드와 동일 관례.
- **roc** — Rate of Change. `(종가 − 종가[period]) / 종가[period] × 100` [%]. 기준 0이면 None.
- **momentum** — `종가 − 종가[period]` (절대차이).
- **trix** — 삼중 EMA 종가의 1봉 변화율 × 100 [%] (Jack Hutson).
- **cmo** — Chande Momentum Oscillator. `100·(ΣUp − ΣDown)/(ΣUp + ΣDown)`, −100..100.
- **ppo** — Percentage Price Oscillator. `(EMA_fast − EMA_slow)/EMA_slow × 100`, 신호선 EMA, 히스토그램.
- **dpo** — Detrended Price Oscillator. `종가[i − (period//2 + 1)] − SMA(종가,period)[i]`.
- **coppock** — Coppock Curve. `WMA(ROC(14) + ROC(11), 10)` (Edwin Coppock).

### OHLC (고·저·종, 일부 시가)
- **cci** — Commodity Channel Index. `(TP − SMA(TP))/(0.015·평균편차)`, TP=(H+L+C)/3. 편차0이면 0.0 (Donald Lambert).
- **williams_r** — `(기간최고 − 종가)/(기간최고 − 기간최저) × −100`, 0..−100. 범위0이면 None (Larry Williams).
- **stochrsi** — RSI 를 자기 기간 고저로 다시 스토캐스틱화(0~100), %K·%D 평활 (Chande & Kroll).
- **adx** — Wilder ADX/+DI/−DI. +DM·−DM·TR Wilder 평활 → DI → DX → ADX(DX Wilder 평균) (J. Welles Wilder).
- **aroon** / **aroon_oscillator** — 최근 신고가·신저가까지의 경과봉 기반 Up/Down(0~100), Oscillator=Up−Down (Tushar Chande).
- **vortex** — VI+ = Σ|H−L_prev|/ΣTR, VI− = Σ|L−H_prev|/ΣTR (Botes & Siepman, 2010).
- **mass_index** — 고저폭 EMA 와 그 EMA 의 비를 period 합산(EMA 9, 합산 25) (Donald Dorsey).
- **awesome_oscillator** — `SMA(중간값,5) − SMA(중간값,34)`, 중간값=(H+L)/2 (Bill Williams).
- **fisher_transform** — 중간값을 기간 고저로 −1..1 정규화 후 피셔 변환(재귀 평활 0.33/0.67, 0.5) (John Ehlers).
- **rvi** — Relative Vigor Index. 4봉 대칭가중한 (C−O)/(H−L) 를 기간 평균, 4봉가중 신호선 (John Ehlers). 시가 필요.
- **keltner** — mid=EMA(종가), upper/lower = mid ± mult·ATR (Chester Keltner / Linda Raschke 변형).
- **parabolic_sar** — Wilder PSAR. AF step 0.02 증가·0.2 상한, 직전 두 봉 극값으로 SAR 클램프 (J. Welles Wilder).
- **ultimate_oscillator** — BP/TR 를 3기간(7·14·28) 가중(4·2·1)/7 합산, 0~100 (Larry Williams).
- **ulcer_index** — 기간 내 고점대비 낙폭(%)의 제곱평균제곱근, 하방 위험 (Peter Martin).
- **historical_volatility** — 로그수익률 표본표준편차(ddof=1)·√annualize·100 [%] (연율화 기본 252).
- **chaikin_volatility** — 고저폭 EMA 의 period 전 대비 변화율 [%] (Marc Chaikin).
- **ichimoku** — 전환선·기준선·선행스팬A/B·후행스팬(9·26·52). 선행/후행 displacement 는 표시용 오프셋이라 미적용(계산 시점 인덱스에 값 배치), chikou=종가 (Goichi Hosoda).
- **pivot_points** — 클래식(플로어) 피벗. 각 봉을 **직전** 봉의 H/L/C 로 산출(P,R1~R3,S1~S3).
- **elder_ray** — 황소힘=고가−EMA(종가), 곰힘=저가−EMA(종가) (Alexander Elder).

### 거래량 기반 — 일봉 bar 의 **선택 항목 `volume`** 이 있을 때만 유효
엔진은 bar 의 `volume` 을 선택 항목으로 정규화한다(`meritz/strategy_engine.py` 의 `_inputs`).
기존 `obv` 와 동일하게 아래 지표도 거래량이 실려 있어야 의미가 있다. 거래량이 없는 데이터에서는
호출하지 않는다(거래량 미제공 시 스킵 대상).
- **vwap** — 누적 Σ(TP·거래량)/Σ거래량, TP=(H+L+C)/3 (세션 누적).
- **vwma** — Σ(종가·거래량)/Σ거래량 (기간 이동).
- **mfi** — Money Flow Index. TP 상승/하락으로 나눈 자금흐름의 RSI 형, 0~100. 음흐름0이면 100 (Quong & Soudack).
- **cmf** — Chaikin Money Flow. Σ(MFM·거래량)/Σ거래량, MFM=((C−L)−(H−C))/(H−L) (Marc Chaikin).
- **force_index** — EMA((종가변화)·거래량) (Alexander Elder).
- **ease_of_movement** — SMA(가격이동/박스비), 박스비=(거래량/scale)/(H−L) (Richard Arms). scale 은 표시 배율.
- **ad_line** — Accumulation/Distribution Line. Σ(MFM·거래량) 누적 (Marc Chaikin).
- **chaikin_oscillator** — `EMA(A/D선,3) − EMA(A/D선,10)` (Marc Chaikin).
- **pvt** — Price Volume Trend. 누적 Σ 거래량·(종가변화율).

### DSL 연동 상태 (등록 완료)
이 확장 지표들은 전략 DSL operand 타입으로 **전부 등록**되었다. operand 해석부는
`runtime/dsl.py` 의 `INDICATORS` 레지스트리이며, 기존 3종(sma·ema·rsi)을 포함해 54종을
노출한다. 공개 스키마(`runtime/strategy.schema.json` 의 `$defs.operand`)는 이 레지스트리에서
생성·동기화하고(드리프트 테스트), SDK(`sdk/meritz_strategy/__init__.py` 의 `get_indicator`)와
파라미터 최적화(`optimizer/parameter_grid.py`)도 같은 레지스트리를 미러링한다. `make_decider`
는 모든 지표 계열을 데이터셋당 1회 precompute 하므로 봉 수에 선형이다(원래 sma/ema/rsi 만
precompute 하던 것을 확장; 인과성 등가로 결정 결과는 바이트 동일). 거래량 기반 지표는
스키마상 유효하나 `volume` 필드가 있어야 값이 나온다(없으면 명확한 오류). 각 지표의 수치 거동은
`tests/test_indicators_expanded.py`, DSL/SDK/스키마 연동은 `tests/test_dsl_indicators.py`·
`tests/test_templates_expanded.py` 에서 검증한다.
