# Meritz Strategy Studio — 베타

> **베타 버전(0.5.0b1)입니다.** 메리츠증권 Open API 시세로 전략을 가상 체결해 보는 로컬 백테스트 도구를
> **소스 코드로** 공개합니다. 설치 파일·서명된 실행 파일은 아직 배포하지 않습니다. 베타 기간에는 기능·화면·저장 형식이
> 바뀔 수 있고, 저장한 전략과 결과가 다음 버전에서 열리지 않을 수 있습니다. 문제와 의견은
> [GitHub 이슈(베타 의견 양식)](https://github.com/meritz-securities/open-api/issues/new?template=strategy-studio-beta.yml)로 받습니다. 개발자 포털 문의 채널의 정식 지원 범위에는 아직 들어가지 않습니다.
> 백테스트 결과는 과거 자료에 대한 사후 계산이며 투자권유나 수익 예측이 아닙니다. 자세한 내용은 [면책 및 이용 조건](DISCLAIMER.md)을 읽어 주십시오.

Studio·CLI·메리츠 MCP 가 같은 Runtime 을 사용합니다. 아래 **메리츠증권 Open API MCP 서버** 절의 공개 릴리스 파일은
이 Studio 를 포함하지 않습니다.

**베타에 들어 있는 것**

- 예제 전략 35종, 비주얼 빌더(DSL) 조건 편집, JSON 고급 편집, Python 코드 편집(macOS 격리 실행·Windows 동봉 실행기)
- 다음 봉 가상 체결, 독립 원장 검산(Doctor), 성과 지표·위험 진단, 후보 재실행·비교, 기간 분리 검증
- 국내·미국 일봉 조회 경로, 사용자 OHLC CSV 가져오기, 연습용 합성 시세. 시세 조회 키는 앱 실행 중 메모리에만 둡니다
- HTML·JSON·CSV 내보내기, 로컬 SQLite 저장과 초기화

**베타 범위** — 소스로 실행해 기본 브라우저로 여는 구성이 기준입니다. 자세한 표는 [베타 범위와 알려진 제한](docs/customer-readiness.md)

- 베타 기준은 브라우저 모드입니다. 자체 창(`--window`)은 소스의 선택 옵션으로 남아 있으며, 정식 제공 범위는 설치 파일(서명된 macOS 앱, Windows 설치본)과 함께 베타 이후입니다.
- 배당·분할 등 기업행사 반영, 다종목·환율은 지원하지 않습니다

**고객용 Studio 는 상용 AI 에 연결하지 않습니다.** AI 키 입력·AI 생성 화면이 없습니다. AI 는 메리츠 MCP(`--mcp`)에 연결한
AI 호스트가 전략을 작성하고 도구로 실행하는 경로만 제공하며, 화면의 개선 후보는 규칙 기반입니다.

## 소스로 실행하기

Python 3.11 이상, [uv](https://docs.astral.sh/uv/), Node.js 20.19 이상(20.x) 또는 22.12 이상이 필요합니다. 한 번만 빌드하면 됩니다.

```sh
git clone https://github.com/meritz-securities/open-api.git
cd open-api/strategy-studio
uv sync
cd studio && npm ci && npm run build && cd ..
uv run python platform_entry.py
```

Windows PowerShell 에서는 `cd studio; npm ci; npm run build; cd ..` 처럼 `;` 로 잇습니다.

| 옵션 | 동작 |
|---|---|
| (없음) · `--browser` | 기본 브라우저로 Studio 를 엽니다. 베타 기본값입니다. |
| `--window` | 자체 웹뷰 창을 시도하고, 열 수 없으면 브라우저로 넘어갑니다. 베타에서는 선택 기능이며 정식 제공은 베타 이후입니다. |
| `--no-browser` | 창을 열지 않고 60초 유효한 일회용 접속 링크만 출력합니다. |
| `--mcp` | stdio MCP 에서 기존 Open API 도구와 전략·백테스트 도구를 함께 제공합니다. HTTP/SSE 에서는 플랫폼 도구를 등록하지 않습니다. |
| `--cli <명령>` | 터미널에서 같은 Runtime 을 사용합니다. 예: `--cli templates` |

기존 MCP 를 통한 `meritz_studio_open` 은 같은 계정 Registry 를 재사용합니다. 독립 실행 Studio 는 다른 프로그램의 MCP 키를 자동 복사하지 않습니다.

[사용 안내](docs/user-guide/platform-local.md) · [전략 예시](docs/user-guide/strategy-templates.md) · [Windows 범위](docs/windows-v1-scope.md) · [베타 범위와 알려진 제한](docs/customer-readiness.md) · [공통 계약](docs/architecture/contracts.md) · [네이티브 번들 빌드](packaging/README.md)

---

# 메리츠증권 Open API MCP 서버

[개발자 포털](https://openapi.imeritz.com) · [API 신청](https://openapi.imeritz.com/api-apply) · [API 문서](https://openapi.imeritz.com/apiservice) · [문의](https://openapi.imeritz.com/qna)

[![test](https://github.com/meritz-securities/open-api-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/meritz-securities/open-api-mcp/actions/workflows/test.yml) [![release](https://img.shields.io/github/v/release/meritz-securities/open-api-mcp?label=%EC%84%A4%EC%B9%98%20%ED%8C%8C%EC%9D%BC)](https://github.com/meritz-securities/open-api-mcp/releases/latest) [![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

MCP를 지원하는 AI 클라이언트에 연결해, 메리츠증권 Open API의 시세·잔고·주문을
대화로 다루는 MCP 서버입니다. 내 PC에서 도는 로컬 서버입니다. 기존 API용 배포본은 단일 실행 파일이라
Python이나 uv를 따로 설치하지 않으셔도 됩니다. Python 코드 백테스트는 위 Studio 베타 또는 아래 MCP 백테스트 도구 절을 따르십시오.

기본값은 조회 전용입니다. 주문·환전은 `MERITZ_READ_ONLY=0`으로 두셔야 열립니다.

## 앱키

[개발자 포털](https://openapi.imeritz.com)에서 앱키(App Key)와 시크릿(App Secret)을 발급받으십시오.
앱키에는 계좌가 묶입니다. 요청에 계좌번호를 넣지 않아도 그 계좌가 조회되고, 주문도 그 계좌로 나갑니다.
실행 파일 안에는 앱키가 없습니다. 설치 화면이나 환경변수로 넣습니다.

## 기존 API용 배포본 설치

파일은 [릴리스 페이지](https://github.com/meritz-securities/open-api-mcp/releases/latest)에서 받으십시오.
쓰시는 것 하나만 받으시면 됩니다.

| 쓰시는 도구 | 방법 |
|---|---|
| Claude Desktop | OS에 맞는 `.mcpb` 파일을 내려받아 더블클릭 — [Mac Apple 칩](https://github.com/meritz-securities/open-api-mcp/releases/latest/download/meritz-open-api-darwin-arm64.mcpb) · [Mac Intel](https://github.com/meritz-securities/open-api-mcp/releases/latest/download/meritz-open-api-darwin-x64.mcpb) · [Windows](https://github.com/meritz-securities/open-api-mcp/releases/latest/download/meritz-open-api-win32-x64.mcpb) |
| 아무 도구나 — 자동 설치 | `git clone` 후 `python3 install.py` — 실행 파일을 내려받고 등록 명령까지 만들어 줍니다 |
| Claude Code | `claude mcp add meritz --env MERITZ_APP_KEY=앱키 --env MERITZ_APP_SECRET=시크릿 -- <실행 파일 경로>` |
| Codex CLI · ChatGPT 데스크톱 | `codex mcp add meritz --env MERITZ_APP_KEY=앱키 --env MERITZ_APP_SECRET=시크릿 -- <실행 파일 경로>` |
| Gemini CLI | `gemini mcp add -e MERITZ_APP_KEY=앱키 -e MERITZ_APP_SECRET=시크릿 meritz <실행 파일 경로>` |
| Cursor · VS Code | [`docs/connect.html`](docs/connect.html)을 내려받아 브라우저로 열면 **한 번에 추가** 버튼이 만들어집니다 |

실행 파일은 [Mac Apple 칩](https://github.com/meritz-securities/open-api-mcp/releases/latest/download/meritz-mcp-darwin-arm64) ·
[Mac Intel](https://github.com/meritz-securities/open-api-mcp/releases/latest/download/meritz-mcp-darwin-x64) ·
[Windows](https://github.com/meritz-securities/open-api-mcp/releases/latest/download/meritz-mcp-win32-x64.exe) 입니다.
Mac에서 직접 내려받으셨다면 `chmod +x`와 `xattr -d com.apple.quarantine`을 걸어 주십시오.
격리 속성이 남아 있으면 서버가 아무 메시지 없이 종료되어 도구 목록에 나타나지 않습니다.

OS 보안 경고, 체크섬 대조, 기동 대기 시간, Claude 웹·ChatGPT 웹을 조회 전용으로
붙이는 터널 연결은
[`docs/install.md`](docs/install.md)에 있습니다. `install.py`가 이 과정을 대신해 줍니다.

## 도구

이 작업 사본을 로컬 stdio로 실행하면 기존 조회·거래 도구와 백테스트 도구가 뜹니다. "삼성전자 현재가 알려줘"처럼
그대로 물어보시면 됩니다. 분류마다 하나씩 여섯 개에, 서버 설정과 카탈로그 요약을 돌려주는
`meritz_info`까지 기존 도구는 일곱 개이고, `api_type`으로 호출할 API를 고릅니다.

| 도구 | 다루는 것 | API |
|---|---|---:|
| `meritz_account` | 잔고·예수금·평가·거래내역·손익 | 12건 |
| `meritz_market` | 국내주식·국내 지수 시세·호가·체결·투자자별 매매·순위·재무·시장정보 | 41건 |
| `meritz_overseas_market` | 해외주식·해외 지수·원자재·환율 시세·호가·체결·순위·조건검색 | 21건 |
| `meritz_trading` | 주문·정정·취소·주문내역·예약주문 | 24건 |
| `meritz_forex` | 환율·환전 | 4건 |
| `meritz_reference` | 거래일·장 운영시간·거래소 등 참조 정보 | 5건 |

국내와 해외 시세를 한 도구에 담지 않은 것은 종목코드와 거래소 코드 체계가 서로 달라서입니다.
섞어 두면 모델이 국내 파라미터를 해외 조회에 그대로 넣습니다.
`api_type='describe'`로 부르면 파라미터와 응답 필드 설명을 돌려줍니다. REST 109건 중
107건을 도구로 노출하고, 토큰 발급·폐기 2건은 서버가 내부에서 처리합니다.

실시간(웹소켓)은 MCP의 요청·응답 모델과 맞지 않아 이 서버에서 다루지 않습니다.
실시간 코드는 [open-api-codegen-mcp](https://github.com/meritz-securities/open-api-codegen-mcp)에서 받으십시오.

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `MERITZ_APP_KEY` / `MERITZ_APP_SECRET` | — | 포털에서 발급 |
| `MERITZ_LABEL` | 첫 번째 계좌 이름 | 계좌를 여러 개 쓸 때 구분하는 이름 |
| `MERITZ_APP_KEY_2` / `MERITZ_APP_SECRET_2` / `MERITZ_LABEL_2` … | — | 두 번째 이상 계좌. 앱키 하나에 계좌 하나가 묶이므로 앱키 목록이 곧 계좌 목록입니다. 이름이 겹치면 시작하지 않습니다 |
| `MERITZ_BASE_URL` | `https://openapi.imeritz.com:9443` | 호출 대상 (https 만) |
| `MERITZ_READ_ONLY` | `1` | 기본이 조회 전용입니다. 주문·환전까지 쓰시려면 `0` |
| `MERITZ_TIMEOUT` | `15` | 요청 제한시간(초) |
| `MCP_TYPE` | `stdio` | `streamable-http`로 두면 HTTP 서버로 뜹니다 |
| `MCP_HOST` / `MCP_PORT` / `MCP_PATH` | `127.0.0.1` / `8000` / `/mcp` | HTTP 모드의 주소·포트·경로 |

**계좌가 둘 이상이면** 도구의 `account` 인자로 계좌를 고릅니다. 일반 조회는 생략하면 첫 번째(기본) 계좌로 나가고,
주문·환전과 백테스트 시세 조회는 생략할 수 없습니다.

## 주문은 두 단계입니다

주문과 환전은 첫 호출에서 전송하지 않고 보낼 내용과 `confirm_token`만 돌려줍니다.
사람이 내용을 확인한 뒤 토큰을 넣어 다시 호출해야 실제로 나갑니다. 토큰은 한 번만 쓸 수
있고 180초 뒤 만료되며, 보낼 내용·대상 서버·앱키 중 하나라도 바뀌면 무효가 됩니다.

이 절차는 실수를 막기 위한 것이지, 사람이 내용을 확인했다는 증거는 아닙니다.
게이트는 `ApiClient.call()` 에 있어, 도구로 호출하시든 같은 패키지의 클라이언트를
파이썬에서 직접 부르시든 똑같이 걸립니다. 한계와 주문 접수 판정은
[DISCLAIMER.md](DISCLAIMER.md)에 적어 두었습니다.

## 저장소가 네 개입니다

| 하고 싶으신 일 | 여기로 |
|---|---|
| AI 클라이언트에 붙이기 | **이 저장소** |
| 파이썬으로 직접 호출해 보기 | [open-api](https://github.com/meritz-securities/open-api) — 실행 예제 |
| 터미널에서 조회·주문하기 | [open-api-studio](https://github.com/meritz-securities/open-api-studio) — `meritz` CLI |
| 호출 코드를 받아 쓰기 | [open-api-codegen-mcp](https://github.com/meritz-securities/open-api-codegen-mcp) — MCP 서버 |

## 개발

```bash
uv sync
uv run pytest tests/ -q

./build_binary.sh                    # bin/meritz-mcp — PyInstaller 단일 실행 파일
python3 make_bundle.py darwin-arm64  # dist/meritz-open-api-darwin-arm64.mcpb
```

`.env.example`을 `.env`로 복사해 쓰셔도 됩니다. **단일 실행 파일은 자기 옆에 둔
`.env`를 가장 먼저 읽습니다** — 앱키를 명령줄에 적지 않아도 되므로 셸 기록과
프로세스 목록에 남지 않습니다. 파일 권한은 `chmod 600` 으로 두십시오. MCP 서버 릴리스 파일은
[open-api-mcp](https://github.com/meritz-securities/open-api-mcp) 저장소에서 빌드·배포하며, 이 폴더에는 릴리스 자동화가 없습니다.

## 사용 전 확인해 주세요

- 대상 서버는 운영이 기본값이고, 앱키에 연결된 계좌는 실계좌입니다. 모의계좌는 없습니다
- 기본값은 조회 전용입니다. 주문·환전은 `MERITZ_READ_ONLY=0`으로 두셔야 열립니다
- 확인 절차는 실수를 막기 위한 것이지, 사람이 내용을 확인했다는 증거는 아닙니다
- 조회 결과는 연결하신 AI 서비스로 전송되며, 해당 서비스의 데이터 처리 정책이 적용됩니다
- 앱키와 시크릿을 저장소에 커밋하지 말아 주세요
- [DISCLAIMER.md](DISCLAIMER.md)를 꼭 읽어 주세요

## 라이선스

MIT — [LICENSE](LICENSE)

실행 파일에 함께 들어가는 오픈소스의 라이선스 전문은
[THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt) 에 있습니다.

문의는 개발자 포털을 이용해 주세요.

## MCP 백테스트 도구 (호환 경로)

기존 `python -m meritz.server` stdio 연결은 아래 호환용 `meritz_backtest_*` 도구 6개를 제공합니다.
Studio와 동일한 저장·실행 경로가 필요한 플랫폼 MCP 연결은 전용 실행 파일의 `--mcp` 옵션을 사용합니다.
이 연결에는 `meritz_platform_*` 및 `meritz_strategy_*` 도구만 등록하고, 아래 호환용 도구는 함께 등록하지 않습니다.
같은 고객 Python 코드를 서로 다른 정책·결과 저장소에서 실행하지 않도록 두 백테스트 실행 경로를 분리했습니다.
HTTP/SSE에서는 사용자별 결과 격리가 없어 두 경로 모두 등록하지 않습니다.

| 도구 | 역할 |
|---|---|
| `meritz_backtest_validate` | 단일 Python 파일의 문법과 `on_bar(ctx)` 진입점 확인. 코드를 실행하지 않음 |
| `meritz_backtest_code` | 사용자 Python 전략을 macOS 격리 환경 또는 실행기가 동봉된 Windows 번들의 제한된 작업 프로세스에서 실행. 시세 출처를 반드시 명시하고 실제 시세의 종목·기간도 지정 |
| `meritz_backtest_demo` | 고정 SMA 전략을 합성 시세로 시연 |
| `meritz_backtest_run` | 고객 키의 국내 일봉으로 고정 SMA 예제 실행 |
| `meritz_backtest_compare` | SMA 결과의 동일 시세·비용에서 이동평균 조건 비교. 사용자 코드 결과에는 적용하지 않음 |
| `meritz_backtest_trade` | 신호일·가상 체결·기록된 설명 조회. 고객 코드의 설명은 독립 검증하지 않음 |

**이 호환 경로의 사용자 코드 실행은 macOS 격리 환경 또는 실행기가 동봉된 Windows 번들에서 됩니다.** 격리를 사용할 수 없으면 실행을 거부합니다.
기존 릴리스의 단일 실행 파일·자동 설치·`.mcpb`를 설치했다고 이 기능이 추가되는 것은 아닙니다.
`build_strategy_lab.sh` 로 만드는 전략 랩 폴더형 번들은 macOS 전용이며, 고객에게 Docker 나 Python 설치를 요구하지 않습니다. 베타에서는 릴리스 자산으로 배포하지 않습니다.
개발자는 `bash build_strategy_lab.sh`로 만들고, 고객 실행 경로는 `local-bundle/meritz-strategy-lab/Start.command`입니다.
폴더 전체를 유지해야 합니다. 합성 시세의 네이티브 실행과 제한 동작을 확인했으며 상세 범위는 백테스트 안내에 있습니다.

고객이 이미 등록한 MCP 계좌의 키로 시세를 조회하고, 키·토큰은 전략 작업 프로세스에 전달하지 않습니다.
MCP Apps 화면은 차트·거래 상세를 제공합니다. 표시 방식은 연결한 AI 호스트의 MCP Apps 지원에 따릅니다.
로컬 브라우저에서 같은 도구를 사용하는 검토 화면도 제공합니다.
전략 코드와 보고서는 연결한 AI 서비스에 전달될 수 있습니다.

사용 방법과 한계는 [백테스트 안내](docs/backtest.md), 코드 규격은 [전략 계약](docs/strategy-contract.md),
예제는 [examples/strategies](examples/strategies)를 참고하십시오.
