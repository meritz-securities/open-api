# 내 Python 전략으로 백테스트하기

고객이 작성한 단일 Python 파일의 `on_bar(ctx)`를 실행하고, 호스트가 가상 체결과 손익을 계산합니다.
시세는 기존 메리츠 MCP에 등록한 고객 계좌의 키로 조회합니다. 키·토큰은 사용자 전략 프로세스에 전달하지 않습니다.
고정 SMA 전략은 화면 시연과 조건 비교용 예제로 별도 제공됩니다.

사용자 코드는 **macOS 격리 환경 또는 실행기가 동봉된 Windows 번들**에서 실행합니다. 이 문서의 폴더형 번들(`build_strategy_lab.sh`)은 macOS 전용입니다.
베타에서는 번들을 릴리스 자산으로 배포하지 않으며, 기존 단일 실행 파일과 `.mcpb`에는 이 기능이 들어 있지 않습니다.
격리를 사용할 수 없는 환경(Linux, 실행기가 없는 Windows)에서는 사용자 코드를 실행하지 않습니다.

## 고객 실행 경로

폴더형 번들의 `Start.command`, `meritz-strategy-lab` 실행 파일과 `_internal` 폴더를 함께 유지하십시오.
`build_strategy_lab.sh`로 만든 `meritz-strategy-lab/Start.command`는 로컬 브라우저에서 전략 실험실을 엽니다. `packaging/build_native.py`가 만드는 별도 `meritz-strategy-platform` 번들은 `platform_entry.py`를 사용해 기본 브라우저로 Studio를 열고, `--window` 로 자체 창을 선택합니다. 두 빌드는 실행 옵션과 패키징 구성이 다릅니다. Docker·Python·Node.js를 별도로 설치하지 않는 폴더형 구성입니다.
빌드 담당자 PC에는 Studio UI 생성을 위해 Node.js/npm이 필요합니다. 고객이 실행할 때 Node.js/npm을 따로 설치할 필요는 없습니다.
이 번들은 베타 기간에 릴리스 자산으로 배포하지 않으므로 공식 다운로드 자산으로 안내하지 않습니다.

1. `.py` 파일을 선택하거나 코드 편집창에 단일 파일 내용을 붙여 넣습니다. 파일 선택만으로는 실행하지 않습니다.
2. 코드 확인 버튼으로 문법과 `on_bar(ctx)` 진입점을 확인합니다. 검사를 통과해도 안전성이나 수익성을 보증하지 않습니다.
3. **메리츠 시세** 또는 **합성 시세**를 명시적으로 선택합니다. 실제 시세에는 종목·기간·기존 MCP 계좌 이름을 지정합니다.
4. 실행 결과의 자산 곡선과 거래를 확인합니다. 거래 설명은 사용자 코드가 제공한 주장이며 별도로 검증한 사실이 아닙니다.

고객 키는 기존 MCP 계좌 설정을 사용합니다. 별도 실행 파일은 다른 설치본의 설정을 자동 복사하지 않으므로,
같은 계좌 환경변수 또는 실행 파일 옆의 `.env` 설정이 이 프로세스에 연결되어 있어야 합니다.
키를 코드·대화·보고서에 적지 마십시오. 코드와 보고서는 연결한 AI 서비스에 전달될 수 있습니다.
로컬 브라우저 검토 경로 자체는 AI 서비스 연결 없이 기존 MCP 도구를 내부에서 호출합니다.

## Claude Desktop 연결

계좌·주문·환전 API 도구 없이 전략 Runtime을 연결하려면 전용 폴더형 실행 파일에 `--strategy-mcp`를 전달합니다. 이 연결에는 Studio 열기와 요청·연구 도구도 포함됩니다.
stdio 연결에서 기존 Open API 도구까지 같은 서버에 결합하려면 `--mcp`를 사용할 수 있습니다. 이 결합 경로는 `meritz_trading`을 포함한 기존 API 도구도 등록하므로, 주문·환전이 필요하지 않으면 `MERITZ_READ_ONLY=1`을 유지하십시오. HTTP/SSE에서는 플랫폼 도구를 등록하지 않습니다.
Claude Desktop에서는 사용할 진입점의 실행 경로와 인자를 명시합니다. 조회 전용을 해제한 경우 기존 주문 미리보기·확인 절차가 적용되지만, 백테스트 신호나 과거 가상 체결을 주문으로 자동 변환하지 않습니다.
자격증명을 대화로 전달하거나 전략 작업 프로세스에 직접 설정할 필요는 없습니다.

요청 예시:

- “첨부한 Python 전략의 문법과 진입점을 확인해줘.”
- “이 코드를 합성 시세로 실행해서 거래 내역을 보여줘.”
- “이 코드를 기존에 등록한 장기투자 계좌의 시세로 실행해줘. 종목 005930, 기간 2025-01-02부터 2025-06-30까지.”

## 도구와 입력

전용 전략랩 번들의 stdio 연결에 `--mcp`를 지정하면 기존 Open API 도구와 플랫폼 MCP 도구가 함께 시작됩니다. HTTP/SSE 연결에서는 플랫폼 MCP 도구를 등록하지 않습니다.
전략·백테스트는 Studio·CLI와 같은 공유 Runtime·저장소를 사용합니다. 전략 전용 연결은 `--strategy-mcp`이며 계좌·주문·환전 도구를 등록하지 않습니다. 주요 플랫폼 도구는 다음과 같습니다.

| 도구 | 역할 |
|---|---|
| `meritz_strategy_templates` / `meritz_strategy_validate` / `meritz_strategy_save` | 전략 예제 조회, 검증, 버전 저장 |
| `meritz_platform_data` / `meritz_platform_backtest` | 합성·메리츠·가져온 시세 저장, 같은 Runtime 백테스트 |
| `meritz_platform_runs` / `meritz_platform_compare` | 실행 결과 조회·비교 |
| `meritz_strategy_diagnose` / `meritz_strategy_experiment` / `meritz_strategy_robustness` | 위험 진단, 변경안 재실행, 검증 실험 |

`python -m meritz.server`로 직접 실행한 기존 stdio MCP에는 아래 호환용 `meritz_backtest_*` 도구가 등록됩니다.
이 구형 경로는 결과를 프로세스 메모리에 보관하며 플랫폼 MCP와 같은 연결에 함께 등록하지 않습니다.
이 구형 도구만 MCP Apps 성과 카드·자산 곡선·거래 선택 화면을 연결합니다. 지원하지 않는 호스트에는 텍스트 요약과 `structuredContent` 보고서 JSON을 반환합니다. 로컬 브라우저 검증은 실제 Claude 호스트의 표시·클릭 검증과 다릅니다.
HTTP/SSE에서는 사용자별 결과 격리가 없어 구형 백테스트 도구를 등록하지 않습니다.

| 도구 | 동작 |
|---|---|
| `meritz_backtest_validate` | `code`의 Python 문법·단일 `on_bar(ctx)` 진입점 확인. 코드는 실행하지 않음 |
| `meritz_backtest_code` | 코드 실행. `source="meritz"` 또는 `source="demo"`를 반드시 명시 |
| `meritz_backtest_demo` | 고정 합성 일봉 160개에 SMA 예제를 적용 |
| `meritz_backtest_run` | 고객 계좌의 국내 일봉에 고정 SMA 예제를 적용 |
| `meritz_backtest_compare` | SMA 실행의 동일 시세·비용에서 이동평균 기간만 변경. API 재호출 없음 |
| `meritz_backtest_trade` | `runId`, `tradeId`로 가상 체결 상세 조회 |

### 구형 도구의 입력 및 결과 보관

`meritz_backtest_code`는 코드 문자열을 받습니다. 임의 파일 경로·API 키·실행 명령은 입력받지 않습니다.
`source`에는 기본값이 없습니다. 실제 시세를 선택하면 `symbol`, `startDate`, `endDate`를 모두 명시하고,
계좌가 여러 개면 `account`도 명시해야 합니다. 종목·기간을 생략하면 조회하지 않습니다.
합성 시세(`source="demo"`)에는 종목·기간·계좌 값을 함께 넣지 마십시오. 혼합 입력은 오류로 반환합니다.
`initialCash`, `feeBps`, `slippageBps`를 설정할 수 있습니다. 기본 100만원·15bp·10bp는 계산 가정이며 실제 고객 수수료가 아닙니다.
현재 코드 편집 화면은 이 기본 비용을 사용하며 변경값 입력은 MCP 도구에서 지원합니다.
사용자 코드 결과를 `meritz_backtest_compare`에 넣으면 오류를 반환합니다. 다른 코드 버전은 각각 실행해야 하며,
현재 사용자 코드 간 동일 시세 고정 비교 기능은 제공하지 않습니다.

결과는 메모리에 최대 16개 보관하며 프로세스 종료 시 사라집니다.

### Python 전략 실행 제한

시세 실패·불충분한 자료·지원하지 않는 실행 환경에서는 오류를 반환하고 합성 시세나 격리 없는 실행으로 대체하지 않습니다.

## 전략과 실행 제한

- 단일 종목 일봉, 매수 포지션만 지원하며 공매도·다중 종목·외부 Python 패키지를 지원하지 않습니다.
- UTF-8 코드 최대 64KiB, `def on_bar(ctx):` 한 개. Python 진입점이 다른 코드는 [계약](strategy-contract.md)에 맞게 변경해야 합니다.
- 작업 프로세스의 기본 벽시계 제한 20초, 각 IPC 송수신 제한 2초, CPU 제한 10초입니다. 호스트는 macOS `libproc`의 `proc_pid_rusage` v0 `phys_footprint`로 압축 메모리를 포함한 사용량을 20ms 기준의 주기 검사로 감시합니다. 기준은 256MiB이며 순간 초과를 완전히 방지하는 강제 상한과 같지 않습니다.
- 코드 실행 전 자체 검사는 파일 읽기·쓰기·파일 메타데이터·홈 폴더·네트워크·자식 프로세스·부모 프로세스 신호·부모 프로세스 인자 조회·민감 환경변수의 9항목입니다. 검사 실패 시 실행하지 않습니다. 시세 조회 전에 수행한 결과를 해당 실행 세션에서 재사용합니다.
- Python 런타임과 필요한 시스템 파일 읽기는 허용됩니다. 파일·네트워크 접근을 사용하는 표준 라이브러리 기능은 제한되며, 모든 표준 라이브러리의 동작을 보장하지 않습니다.
- 실행 파일 경로 확인에 필요한 상위 디렉터리의 메타데이터와 `uname`에 필요한 OS·장비 이름 5개 sysctl 항목만 추가 허용합니다. 다른 파일의 메타데이터 및 부모 프로세스의 인자·환경 조회는 자체 검사에서 거부를 확인합니다.
- 프로세스 정보 조회는 `process-info*`를 명시적으로 거부합니다. 부모 정보 검사는 C 함수 준비가 성공한 뒤 실제 호출이 권한 오류로 거부됐는지 확인하며, 준비 중 오류는 차단 성공으로 인정하지 않습니다.
- 작업 프로세스에는 현재 봉까지의 시세와 가상 잔고만 전달합니다. 미래 시세·계좌 키·호스트의 성과 계산 권한은 전달하지 않습니다.
- `MERITZ_` 환경변수 중 `.env` 자동 읽기를 막는 `MERITZ_NO_ENV_FILE=1`만 작업 프로세스에 허용합니다. 계좌 키·시크릿·토큰을 넘기는 설정은 아닙니다. 격리 정책은 전역 파일 메타데이터 조회나 전역 `sysctl` 읽기를 허용하지 않습니다.
- 사용자가 반환하는 것은 매수·매도·유지 판단뿐입니다. 실제 주문 API를 호출하지 않습니다.

OS 격리 자체 검사는 알려진 접근 경로에 대한 검사이며, 모든 악성 코드에 대한 안전성 인증은 아닙니다.
macOS의 해당 격리 기능을 사용할 수 없으면 기능이 중단됩니다.

## 계산·데이터 한계

종가까지 관측한 판단을 **다음 봉 시가**에 수수료와 슬리피지를 반영해 체결합니다.
마지막 봉의 판단은 체결하지 않으며 보유분을 마지막 종가로 평가하므로 수익률에 미실현 손익이 포함됩니다.
비교선은 첫 봉 시가에 동일 비용으로 매수 후 보유한 값이며 지수가 아닙니다.

실제 시세는 `market_candles_days`의 TJ, `exch_use_yn=Y`, `mod_stpr_cls_code=0`으로 요청합니다.
최대 365일이며 한국 시간 당일·미래 봉은 제외합니다. 수정주가 **요청**과 모든 OHLC·기업행사의 정확한 **보정 확인**은 다릅니다.
날짜·가격·중복·정렬을 검사하지만 거래소 달력 대조와 기업행사 완전성 검증은 하지 않습니다.
세금·호가 단위·거래량별 체결 제한·거래정지·배당 현금흐름·분할 수량 변동을 재현하지 않으며 실제 투자 성과를 보장하지 않습니다.
실행 ID에는 코드 해시·데이터·비용·판단 기록이 반영됩니다. 코드가 현재 시각 등 변동 조건에 의존하면 반복 결과가 달라질 수 있습니다.

## 개발자용 빌드

Python 3.11 이상에서 저장소 의존성을 설치한 뒤 소스 모드로 검토할 수 있습니다.

```bash
uv sync --all-extras --dev
uv run python -m meritz.backtest_local
```

macOS 폴더형 번들은 `strategy-studio` 폴더의 `build_strategy_lab.sh`로 빌드합니다. 빌드 담당자 PC에는 Node.js 20.19 이상(20.x) 또는 22.12 이상이 필요하며, 스크립트가 `studio/`에서 `npm ci`와 `npm run build`를 실행한 뒤 네이티브 실행 파일·Python 런타임·화면·라이선스를 한 폴더로 묶습니다. 고객 PC에는 Node.js가 필요하지 않습니다.
기존 MCP Apps 화면은 별도 경로인 `ui/backtest`에 있습니다. 그 화면을 수정할 때는 해당 디렉터리에서 의존성과 HTML을 따로 빌드합니다.

```bash
bash build_strategy_lab.sh
./local-bundle/meritz-strategy-lab/Start.command
```

기본 출력은 `local-bundle`, PyInstaller 작업 파일은 `build/strategy-lab`에 둡니다. `STRATEGY_LAB_OUTPUT_ROOT`와 `STRATEGY_LAB_WORK_ROOT`로 경로를 바꿀 수 있으며, 상대 경로는 `strategy-studio` 폴더 기준입니다.

생성되는 `.spec` 파일은 빌드 산출물입니다. 고객에게는 `local-bundle/meritz-strategy-lab` 폴더 전체를 전달합니다.
이 번들은 서명·공증된 공식 설치본이 아닙니다.
기존 `build_binary.sh`와 `make_bundle.py`는 이 전용 폴더형 번들을 대신하지 않습니다.
UI는 외부 CDN이나 브라우저의 시세 API 직접 호출을 사용하지 않습니다.
`npm run preview`의 `/mcp-app.html?preview=1`은 합성 결과 미리보기이며 사용자 코드 실행이나 Claude 호스트 검증이 아닙니다.

## 네이티브 번들 확인 범위

로컬 폴더형 번들에서는 합성 시세로 다음 동작을 확인했습니다.

- 별도 Python·Node·Docker 실행 경로를 지정하지 않은 네이티브 시작과 동봉 `statistics` 모듈 사용
- 작업 프로세스의 고객 키 환경변수 제외와 파일·홈·네트워크·프로세스 등의 격리 자체 검사
- 실제 사용자 코드 변경에 따른 가상 매수 1건과 유지 0건, 서로 다른 호스트 계산 결과
- 무한 반복·과도한 출력의 중단, 호스트 파일·자식 프로세스 접근 차단, 실패 후 다음 정상 실행

개발자는 네이티브 번들을 빌드한 뒤 아래 시험을 실행할 수 있습니다. 합성 시세만 사용하며 API를 호출하지 않습니다.

```bash
uv run python tests/smoke_strategy_bundle.py "${STRATEGY_LAB_OUTPUT_ROOT:-local-bundle}/meritz-strategy-lab/meritz-strategy-lab"
```

이 확인은 모든 macOS 버전·모든 악성 코드·실 API·Claude 대화창 동작을 보증하지 않습니다.

화면 규격: [MCP Apps 공식 문서](https://apps.extensions.modelcontextprotocol.io/api/documents/overview.html).
