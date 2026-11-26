# 공통 계약 v1 (구현 기준)

새 플랫폼은 기존 `meritz.strategy_engine.run_strategy`만 가상 회계 원천으로 사용한다. legacy result schemaVersion=1을 유지하고 필드를 추가한다. 플랫폼 service 결과는 별도 envelope로 감싼다. 원시 주문 API를 service action으로 제공하지 않는다.

## Strategy definition

```json
{"schemaVersion":1,"kind":"dsl","name":"이평 교차","entry":{"op":"cross_above","left":{"type":"sma","period":5},"right":{"type":"sma","period":20}},"exit":{"op":"cross_below","left":{"type":"sma","period":5},"right":{"type":"sma","period":20}},"quantity":1,"allowPyramiding":false,"risk":{"stopLossPct":null,"takeProfitPct":null}}
```

Python: `{schemaVersion:1,kind:"python",name,code}`. `code`는 최상위 `on_bar(ctx)`를 정의하며 optional `initialize(ctx)`는 첫 판단 전에 한 번 호출한다. 일반 Python의 완전 결정성은 보장하지 않는다.

DSL operands: `{type:"field",field:"open"|"high"|"low"|"close"}`, `{type:"constant",value:number}`, 그리고 `indicators/calc.py`의 54개 지표(`sma`·`ema`·`rsi` 포함 — `{type,period:integer,...}`). 각 지표 operand의 필드명은 calc 함수 인자와 동일하고, 다중출력 지표(macd·bollinger·stochastic·adx·keltner·donchian·aroon·vortex·ichimoku·pivot_points·elder_ray·rvi·ppo·stochrsi)는 필수 `component`로 계열을 고른다(예: `{type:"macd",component:"signal",fast:12,slow:26,signal:9}`, `{type:"bollinger",component:"upper",period:20,num_std:2}`). 필수 파라미터가 없는 지표는 생략 시 calc 기본값을 쓴다. 거래량 기반 지표(obv·vwap·vwma·mfi·cmf·force_index·ease_of_movement·ad_line·chaikin_oscillator·pvt)는 스키마상 유효하지만 모든 봉에 `volume` 필드가 있어야 값이 나오고 없으면 명확한 오류다. operand union의 정본은 `runtime/strategy.schema.json`($defs.operand)이며 `runtime.dsl.INDICATORS` 레지스트리에서 생성·동기화된다(드리프트 테스트로 보증). Conditions: `{op:"gt"|"gte"|"lt"|"lte"|"cross_above"|"cross_below",left,right}` 또는 `{op:"all"|"any",conditions:[...]}`. 최대 깊이8/노드64/period2..250/이름80자. warmup의 undefined 값은 false. cross는 이전<=·현재> 또는 이전>=·현재<. 매도 우선, 한 봉 하나의 intent, 보유 없으면 매도 없음. quantity null은 엔진 기본 전액/전량, 정수는 지정 수량. allowPyramiding=false는 보유 중 추가 매수 금지. 손절/익절은 종가 판단 후 다음 봉 체결이며 intrabar stop 보장 아님.

## Data and Run

Dataset `{id,contentHash,source,bars}`. bars 날짜 오름차순2..5000 OHLC, optional volume. source kind synthetic/meritz/imported, label, symbol/market/currency/timezone/adjustment provenance. credentials/account를 저장하지 않는다. Import는 JSON/CSV contents를 검증하고 외부 경로를 받지 않는다.

RunRequest `{revisionId,datasetId,settings:{initialCash,feeBps,slippageBps,taxBps},parentRunId?}`. source=meritz 요청은 기존 account 선택을 통해 데이터 snapshot 생성 이후 datasetId로 실행한다. taxBps는 매도 거래대금에만 적용한다. 현재 통화는 단일 데이터셋 표시통화이며 환율 환산 없음.

Run envelope `{id,revisionId,datasetId,parentRunId,status,manifest,report,analysis,createdAt}`. `id`는 실행 attempt UUID, `report.runId`는 기존 내용 hash, manifest에는 strategyHash/dataHash/engineVersion/resultHash/numeric settings. 상태 queued/running/succeeded/failed/cancelled/interrupted. 무작위 attempt ID와 fetchedAt을 결과 재현성 hash에 넣지 않는다.

## Broker additions (backward compatibility)

기존 필수 settings3개는 그대로 허용. optional taxBps(default0), rejectInsufficient(default false: 기존 오류 정책). 새 플랫폼은 true를 지정하고 rejected 주문 이벤트를 남긴다. Decision 기존필드 + orderType(market 기본/limit),limitPrice(limit필수),evidence(닫힌 JSON형·크기 제한). 한 봉 하나, 다음 한 봉만 유효(DAY); 미체결은 expired. buy limit은 open<=limit면 open, 아니면 low<=limit면 limit; sell은 대칭. 슬리피지 적용값은 limit 경계 내에서 clip. 매도 과수량/현금부족은 명시 정책으로 처리. 세금·비용은 host 계산.

Trade 추가 필드: orderPrice, gross, tax, cashAfter, sharesAfter, averageCostAfter, realizedPnl, cumulativeRealizedPnl, evidence. weighted average cost는 매수 수수료 포함, 매도 실현손익은 매도 수수료와 세금 차감. Portfolio 관측에 averageCost,realizedPnl,unrealizedPnl 추가. report에는 orders 및 decisions/event trace와 finalPortfolio 추가. 기존 trade 필드 유지.

기업행동 실제 처리 미지원: adjustment provenance를 표시하고 배당/분할 현금·수량 조정을 처리했다고 주장하지 않는다. 계약에 기업행동 이벤트가 포함되면 명시적으로 unsupported를 반환하고 조용히 무시하지 않는다.

## Runtime API / adapters

`StrategyRuntime(root=None,catalog=None,registry=None)`는 다음 메서드를 제공한다. SQLite는 앱 소유 root 아래 저장하고 모든 revisions/runs/datasets는 immutable이다.

- `templates()` → `{templates:[definition...]}`
- `validate_strategy(definition)` → `{valid,errors,definition?}`
- `save_strategy(definition,parentRevisionId=None,editorLayout=None)` → `{id,strategyId,parentRevisionId,definition,hash,createdAt}` 및 선택적 `editorLayout`. DSL 노드 좌표 메타데이터는 [레이아웃 계약](editor-layout-contract.md)을 따르며 정의 해시·실행 계산에 포함하지 않습니다.
- `list_strategies()` → `{strategies:[revision...]}`
- `import_data(bars,source)` → dataset metadata
- `demo_data()` → synthetic dataset metadata (명시적 요청만)
- `fetch_data(symbol,startDate,endDate,account=None)` → 기존 국내 어댑터 snapshot; 해외미지원이면 오류
- `list_data()` → `{datasets:[metadata...]}`
- `run(revisionId,datasetId,settings,parentRunId=None)` → Run envelope (sync bounded)
- `get_run(runId)` / `list_runs()` → envelope / `{runs:[summaries...]}`
- `diagnose(runId)` → deterministic analysis
- `experiment(runId,definition=None,settings=None)` → child Run; 같은 dataset 재사용
- `propose(runId,request)` → deterministic 후보3개와 근거; LLM 생성으로 표시하지 않음. 실제 AI host는 definition을 작성하여 experiment 호출 가능
- `compare(runIds)` → 동등 데이터/비용 정보와 실행별 성과/미개선 포함
- `validate_robustness(runId,method,options)` → bounded OOS/비용민감도/MonteCarlo; 지원 method만 공개
- `export_report(runId,format)` → `{filename,mimeType,content}`; HTML/JSON/CSV 및 PDF는 별도실제 지원확인

Studio loopback: POST `/api/platform` `{action,arguments}` → `{ok:true,result}` or `{ok:false,error:{code,message}}`. 인증 header `X-Platform-Token`, 일회용 URL fragment ticket 교환으로 받은 메모리 session token. Host/Origin exact loopback, no wildcard CORS, content-size/worker budget, no credentials in UI. CLI와 MCP는 위 service를 직접 호출한다. MCP 이름은 `meritz_strategy_*`, `meritz_platform_*` 기존 namespace 준수. 저장 동작에 readOnlyHint=false, 실제 주문 도구 노출 없음.

## Analytics boundary

`analytics.analyze(report,manifest=None)`는 유한 JSON 결과: `{metrics,monthlyReturns,diagnostics,assumptions}`. 지표는 CAGR/Sharpe/Sortino/winRate/profitFactor/realizedPnl/unrealizedPnl; 표본부족·분모0·무손실은 null+사유, Infinity 금지. 252거래일·무위험0 가정과 calendar CAGR을 명시. Doctor diagnostic은 `{id,severity,status,title,message,evidence,threshold}`; 사용자 reason은 계산 증거가 아님.

OOS는 chronological split을 고정하고 학습/검증의 선택·평가를 분리한다. Monte Carlo는 seed고정 daily return resampling 가정을 표시하고 미래 예측이라고 표현하지 않는다. 최적화는 수십개 이하 budget, 실패 후보도 결과에 보존한다.

## 통합 검토 수정 규칙 (2026-11-12)

- 프로세스마다 service 객체는 있으나 모든 엔진 실행은 동일 앱 저장소의 OS 배타 파일 잠금을 획득한다. SQLite WAL transaction이 저장을 직렬화한다. 시작 시 무조건 orphan 처리하지 않는다. 실행 잠금을 획득한 후에만 남아 있는 running attempt를 interrupted로 표시한다. 두 프로세스 경쟁 테스트로 살아 있는 작업이 변경되지 않음을 검증한다. 프로세스별 cancel은 해당 프로세스 소유 작업에만 적용한다.
- 엔진은 shared-broker-2로 명시적 version bump. 기존 입력의 수치·동작 호환을 유지하되 runId는 의도적으로 바뀐다. 과거 실행 결과/버전의 runId를 새 버전으로 덮어쓰거나 동일하다고 주장하지 않는다. installed 이전제품은 이번 개발 중 변경하지 않는다.
- canonical JSON은 UTF-8, ensure_ascii=False, sort_keys=True, separators comma/colon, allow_nan=False를 적용한다. 숫자1과1.0 및 Unicode normalization 차이는 입력 표현 차이로 hash가 달라질 수 있다. 동일 저장된 strategy/dataset snapshot을 사용하는 Studio/MCP/CLI는 같은 resultHash를 생성한다. attempt ID/createdAt/elapsed는 report hash에서 제외하며 analytics는 포함하지 않는다. rename은 revision을 생성하며 현재 strategy hash에 name이 포함된다. 의미적 동등성을 나타내는 hash라고 부르지 않는다.
- worker/runner/initialize 변경은 기존 sandbox 검토를 승계하지 않는다. 최종 native에서 9개 probe와 공격·복구 검사를 다시 수행하고 hash를 새로 고정한다. Python은 full-history 비용을 고려하여 플랫폼 경로 최대1,000봉, DSL5,000봉. legacy 도구 한계는 별도 유지.
- evidence의 최상위 origin은 호스트만 지정한다: user-asserted(Python), dsl-evaluated(DSL), host-computed(내장전략). 사용자가 넣은 origin은 details 내부 주장이다. Doctor는 reason이나 user annotation을 회계 근거로 사용하지 않는다.
- Doctor는 fill primitive(수량/가격/비용/세금)로 Decimal 원장을 독립 재계산하여 cash/shares/averageCost/realizedPnl를 대조한다. 금액 허용오차 max(1e-6, absolute scale*1e-10), 정수 수량은 정확 비교. lastClose가 없으면 보유분 최종가치 검증은 insufficient_data다.
- 현재 out_of_sample 메서드는 이미 전체 기간으로 실행한 전략을 사후 분할한다. holdoutAccessed=true, holdoutContaminated=true, trialCount와 선택편향 경고를 반환한다. strict blind OOS, 파라미터 탐색을 포함한 Walk-forward 최적화는 미지원이며, 고정 후보의 사후 학습·평가 선택은 아래 Walk-forward 후보 선택 계약에서 지원이며 정식 지원 표시를 금지한다. 원본/후보/실패 attempt 모두 보존한다.
- SDK와 DSL의 SMA/EMA/RSI는 기존 MIT indicators/calc.py를 공유한다. legacy SMA는 호환 알고리즘의 수치 결과를 유지하며, DSL과 legacy preset의 boundary 동등성을 보장하지 않는다. 새SDK와DSL의 지표 warmup/교차 규약은 동일하다.
- Studio GET은 capability를 포함하지 않는다. native/MCP가60초 일회용 ticket을 URL fragment에 전달하고 브라우저가 즉시 제거한 후 POST /api/session으로 session token을 받아 메모리에 보관한다. plain URL·다른 Origin·사용한 ticket은 자료 조회 권한을 얻지 못한다. 관리자/root·같은 사용자 권한의 악성 프로그램은 방어범위 밖이다.
- `meritz_studio_open`은 기존 MCP 내부의 같은 Registry/Runtime을 사용해 Studio를 연다. 키를 별도 복사/추출하지 않는다. 독립 실행은 자체 env credential이 없는 한 Claude의 암호화된 설정을 자동으로 읽지 못한다. 이를 자동키공유로 표현하지 않는다.
- HTML은 모든 텍스트를 escape, script 차단 CSP 적용. CSV는 formula 선행문자를 neutralize. 지정가 매수 가능액은 슬리피지 및 limit으로 결정된 최종 체결 가격을 사용한다. 호가단위 미지원, tax0 매도는 Doctor에서 경고한다.
- 해외3년 데이터·WindowsPython·서명/공증·신규OS설치는 전체 요청의 미완료 관문이다. 국내 365일 조회·파일 가져오기가 되는 것만으로 정식 v1 전체 완료로 부르지 않는다.

trialCount는 해당 datasetId의 모든 전략·성공/실패 실행 수를 보수적으로 센 값입니다. MCP 실행 응답은 요약과 실행 ID를 반환하며 전체 시계열은 Studio/내보내기로 조회합니다. 기존 엔진 대비 benchmark 수량이 현금 부족 경계에서 한 주 감소하도록 보정되어 benchmarkReturnPct가 달라질 수 있습니다. 전략 체결 회계와 구분한 수정입니다. 저장 hash는 우발적 손상 탐지이며 같은 OS 계정의 악의적 DB 변조 방지를 보장하지 않습니다.

통합 후 저장 정책: 개별 snapshot 16MB 제한은 유지하되, 복구 수단 없는 10,000개 전역 객체 제한은 제거했습니다. SQLite 파일은 사용에 따라 커지며 자동 삭제하지 않습니다. 대량 보관 관리 UI는 미구현이고 디스크 공간 부족은 실패 상태로 보고합니다. 실행 목록은 v2 metadata index/summary를 읽으며 전체 report를 파싱하지 않습니다.

대용량 후속 보완: SQLite schema3는 canonical JSON hash를 유지하며 256KB 이상 payload를 무손실 zlib BLOB으로 저장합니다. 저장본 최대16MB/해제본 최대128MB를 각각 제한합니다. 구형 TEXT snapshot을 읽으며 schema3를 모르는 구버전 앱으로 역실행하지 않습니다. 검증의 비용/OOS 후보는 개별 runId로 저장하고 검증 응답에는 지표·ID만 반환합니다. 실패 후보도 별도 실행 기록에 남습니다.

Python 정적 검증은 AST 파싱 후 실행 없는 컴파일까지 수행합니다. 최상위 실행문은 실행하지 않습니다. 실패 응답 `errors`에는 `message`와 가능한 경우 1부터 시작하는 `line`, `column`을 보존합니다. `column`은 Unicode 문자 기준이며 UTF-16 편집기는 해당 줄을 기준으로 변환해야 합니다. 위치를 특정할 수 없는 계약 오류는 `line: null`이며 일반 스키마 오류에는 위치 필드가 없을 수 있습니다. 통과는 라이브러리 존재·실행 성공·보안 인증을 뜻하지 않습니다.

컴파일 설정은 기존 worker의 postponed annotations 동작을 명시적으로 유지합니다(`annotations` future flag, `dont_inherit=True`, `optimize=0`). Python 3.11의 compiler 오류 UTF-8 byte column은 Unicode column으로 변환합니다. 파서 호출 전 반복형 토큰 검사로 문장당512토큰/전체8192토큰/일반 토큰의 괄호·들여쓰기32단계/f-string256문자를 제한합니다. 긴 식은 여러 문장으로 나눠야 합니다. 이는 재현된 중첩 입력의 검사 프로세스 종료를 막는 제한이며, 정적 검증을 완전한 보안 격리라고 주장하지 않습니다. 실제 사용자 코드 실행의 기존 OS 격리·자원 감시는 그대로 적용합니다.

복잡도 초과 오류에도 해당 토큰의 행·열과 나누어 작성할 안내를 제공합니다. 단항 연속64개, 문장당 연산자128개·lambda/if/for32개, 같은 if의 elif128개와 전체 if/elif256개를 별도로 제한합니다. 전체 한도는 else 안에 새로운 if/elif 연결을 중첩하는 경우에도 적용됩니다. Python 3.11 토크나이저에서 f-string 내부는 불투명하므로 그 내부에 괄호32단계 제한을 적용했다고 주장하지 않습니다. 256문자 상한 및 별도 작은 스택 스레드 시험을 사용합니다. 매개변수32개 dict와 숫자100개 list는 허용 시험을 통과했습니다.

## Walk-forward 후보 선택 계약

`validate_robustness(runId, "walk_forward", options)`는 완료된 원본 실행의 비용·초기자본·시세 snapshot을 고정합니다. `options`는 필수 정수 `trainBars`, `testBars`(각2~5000)와 선택 목록 `candidateRevisionIds`(기준 리비전을 제외한 서로 다른 추가 리비전 최대3개)만 허용합니다. Python 후보의 구간은 기존 history 제한에 따라1000봉 이하입니다. 기준 리비전은 후보 목록 맨 앞에 자동 포함합니다.

학습 구간 `[i*testBars, i*testBars+trainBars)`에서 각 후보를 실제 공통 엔진으로 계산하고, 유한한 `totalReturnPct`가 가장 큰 후보 하나를 바로 뒤 평가 구간에 실행합니다. 동률이면 고정 후보 순서를 사용합니다. 후보 중 하나라도 학습에 실패하면 해당 구간의 선택·평가를 생략하고 실패를 표시합니다. 평가 실패 후 다른 후보를 대신 선택하지 않습니다. 최대6개 완전한 평가 구간·30회 실행이며, 남는 마지막 봉 수는 `droppedTailBars`로 표시합니다. 6구간 초과를 자동으로 잘라내지 않고 입력을 거절합니다.

각 학습·평가 실행은 자금·포지션·전략 상태·지표 준비 구간을 독립 초기화합니다. 평가 구간끼리는 겹치지 않지만 이후 학습 구간에 과거 평가 봉이 들어갈 수 있습니다. 앞 구간 자산을 이어 붙인 전체 수익률·MDD를 제공하지 않습니다. 후보 목록이 이미 전체 데이터 관측 후 만들어졌을 가능성이 있고 원본 실행도 전체 데이터를 사용했으므로 `holdoutContaminated=true`입니다. 학습 구간만 사용한 프로그램 내 선택과 미관측 데이터 보장은 다른 개념입니다.

모든 계산은 기존 `_run_locked`를 사용하며 보고서는 개별 run에 저장합니다. 검증 결과에는 구간 날짜·후보별 지표·선택 리비전·runId·데이터 hash만 반환합니다. 동일 구간을 후보마다 복제 저장하지 않습니다. 검증 작업은120초 후 공통 취소 신호를 설정합니다. 실행 중 콜백은 기존 취소 검사 시점에 중단되므로 강제 실시간 종료 보장은 아닙니다. 종료된 job에는 성공·실패한 child run ID를 보존합니다. `get_validation(validationId)` 및 MCP `meritz_strategy_validation_result`로 저장된 결과를 다시 읽습니다.

검증 시도 수는 `candidateCount`, `foldCount`, `childRunCount`로 이번 작업만 표시합니다. `fullSnapshotRunCount`는 원본 전체 snapshot의 실행 수이며 구간 실행이나 과거 검증 누적 횟수가 아닙니다. 이를 총 실험 횟수로 표시하지 않습니다. 개별 실행에는 생성 시 `validationJobId`를 기록하므로 프로세스 중단 후에도 작업과 연결할 수 있습니다. 취소·중단 시 완료된 구간 표 전체가 저장된다는 보장은 없으며 원본/검증 작업 ID로 연결된 개별 실행이 근거입니다.

새로 생성하는 구간 데이터는 `derivedFrom`과 `validationJobId`로 출처를 연결하고 일반 데이터 선택 목록에서 제외합니다. 출처의 기간 필드가 있으면 실제 구간 날짜로 조정합니다. 이전 버전에서 이미 생성한 무표시 구간 데이터는 소급 분류하지 않습니다. 실행 목록은 기존 최근100개 제한이 남아 있으므로 오래된 개별 실행은 보존한 runId로 조회해야 합니다. 이 제한을 전체 기록 탐색 기능 완료로 간주하지 않습니다.
