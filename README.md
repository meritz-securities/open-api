# 메리츠증권 Open API — Python 예제

> 현재 베타 서비스 기간입니다.

[개발자 포털](https://openapi.imeritz.com) · [API 신청](https://openapi.imeritz.com/api-apply) · [API 문서](https://openapi.imeritz.com/apiservice) · [문의](https://openapi.imeritz.com/qna)

[![test](https://github.com/meritz-securities/open-api/actions/workflows/test.yml/badge.svg)](https://github.com/meritz-securities/open-api/actions/workflows/test.yml) [![python](https://img.shields.io/badge/python-3.11%2B-3776AB)](https://www.python.org/downloads/) [![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

메리츠증권 Open API를 파이썬에서 호출하는 예제 모음입니다. 인증·시세·계좌·주문·실시간을
파일 하나에 주제 하나씩 담았습니다. 라이브러리가 아니라 예제이므로, 필요한 부분을 복사해
프로젝트에 맞게 고쳐 쓰시면 됩니다.

API 목록과 파라미터·응답 필드의 정본은 [개발자 포털](https://openapi.imeritz.com)입니다.
이 저장소가 다루는 것은 호출하는 방법과 그 코드입니다.

## 앱키

[개발자 포털](https://openapi.imeritz.com)에서 앱키(App Key)와 시크릿(App Secret)을 발급받으십시오.
앱키에는 계좌가 묶입니다. 요청에 계좌번호를 넣지 않아도 그 계좌가 조회되고, 주문도 그 계좌로 나갑니다.

## 설치와 첫 호출

```bash
git clone https://github.com/meritz-securities/open-api.git
cd open-api
pip install -e .

export MERITZ_APP_KEY=발급받은_앱키
export MERITZ_APP_SECRET=발급받은_시크릿

python examples/02_market.py 005930
```

```
삼성전자  71800원  전일대비 900 (1.27%)
  매도1 71900  매수1 71800
  일봉 40건
```

`.env.example`을 `.env`로 복사해 쓰셔도 됩니다.

## 코드로 쓰실 때

```python
from meritz import client, load_catalog
cat, c = load_catalog(), client()

res = c.call(cat.get("market_prices"), {"mrkt_div_code": "J", "iscd": "005930"})
```

응답은 `res["ok"]`로 먼저 가르고, 자료는 `res["body"]["data"]`에서 꺼내십시오.
자료가 있는지는 `rsp_cd`가 아니라 `data`가 비었는지로 판정합니다.
응답 판정 규칙 전체는 [`llms.txt`](llms.txt)에, 오류 코드표는 [`docs/errors.md`](docs/errors.md)에 있습니다.

## 예제

| 파일 | 내용 |
|---|---|
| [`01_auth.py`](examples/01_auth.py) | 접근 토큰 발급과 재사용 |
| [`02_market.py`](examples/02_market.py) | 국내 현재가·호가·일봉 |
| [`03_overseas_market.py`](examples/03_overseas_market.py) | 해외 현재가 |
| [`04_account.py`](examples/04_account.py) | 예수금·평가·보유종목 |
| [`05_order.py`](examples/05_order.py) | 주문 — 확인 없이는 전송되지 않는 형태 |
| [`06_paging.py`](examples/06_paging.py) | 연속 조회 |
| [`07_realtime.py`](examples/07_realtime.py) | 실시간 구독 (WebSocket) |
| [`08_forex.py`](examples/08_forex.py) | 환율 조회 |

`meritz/`는 예제마다 인증·판정 코드를 반복해 적지 않으려고 모아 둔 공통 코드입니다.
토큰 관리, 요청 전송, 응답 판정, 호출 간격 제어가 들어 있습니다.

받아 온 데이터로 무엇을 계산하는지 보여드리는 예제가 두 묶음 더 있습니다.
**둘 다 주문을 전송하지 않으며, 투자권유·투자자문이 아닙니다.**

| 디렉터리 | 내용 |
|---|---|
| [`indicators/`](indicators/) | 공개된 표준 기술지표 11종 — 이동평균 교차·RSI·볼린저·MACD·스토캐스틱·ATR·OBV·돈치안 등 |
| [`position_sizing/`](position_sizing/) | 예산을 나눌 때의 회차별 금액·누적 평단, 감수할 손실 금액에서 역산한 수량 |

각 디렉터리의 README에 계산 방법과 한계를 적어 두었습니다. 산출값을 주문에
연결하기 전에 데이터·종목·수량·가격을 직접 검토하십시오.

## 기계가 읽는 명세

| 파일 | 내용 |
|---|---|
| [`openapi.json`](openapi.json) | REST 62건 — OpenAPI 3.1 |
| [`asyncapi.json`](asyncapi.json) | 실시간 13건 — AsyncAPI 3.0 |
| [`CATALOG.md`](CATALOG.md) | 카탈로그 파일이 무엇이고 어떻게 갱신하는지 |

포털 등록 명세를 표준 형식으로 낸 파일입니다. Postman·Insomnia 같은 도구나 코드 생성기에
그대로 넘기실 수 있습니다. 포털이 바뀌면 다시 생성합니다.

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `MERITZ_APP_KEY` / `MERITZ_APP_SECRET` | — | 포털에서 발급 |
| `MERITZ_BASE_URL` | `https://openapi.imeritz.com:9443` | REST 호출 대상 |
| `MERITZ_WS_URL` | `wss://openapi.imeritz.com:29443/websocket` | 실시간 접속점 |
| `MERITZ_READ_ONLY` | `1` | 기본이 조회 전용입니다. 주문·환전까지 쓰시려면 `0` |
| `MERITZ_TIMEOUT` | `15` | 요청 제한시간(초) |
| `MERITZ_CATALOG` | — | 동봉 카탈로그 대신 쓸 `catalog.json` 경로 |
| `MERITZ_NO_ENV_FILE` | — | `1`이면 `.env`를 읽지 않습니다 |

`MERITZ_BASE_URL`을 바꾸실 때는 `MERITZ_WS_URL`도 함께 바꾸십시오. 한쪽만 바꾸면
토큰과 실시간 구독이 서로 다른 서버로 나갑니다.

## 저장소가 네 개입니다

| 하고 싶으신 일 | 여기로 |
|---|---|
| 파이썬으로 직접 호출해 보기 | **이 저장소** |
| 터미널에서 조회·주문하기 | [open-api-studio](https://github.com/meritz-securities/open-api-studio) — `meritz` CLI |
| AI 클라이언트에 붙이기 | [open-api-mcp](https://github.com/meritz-securities/open-api-mcp) — MCP 서버 |
| 호출 코드를 받아 쓰기 | [open-api-codegen-mcp](https://github.com/meritz-securities/open-api-codegen-mcp) — MCP 서버 |

## 개발

```bash
pip install -e ".[dev]"
pytest tests/ -q
```

## 사용 전 확인해 주세요

- 대상 서버는 운영이 기본값이고, 앱키에 연결된 계좌는 실계좌입니다. 모의계좌는 없습니다
- 기본이 조회 전용입니다. 주문·환전을 쓰시려면 `MERITZ_READ_ONLY=0`으로 여셔야 합니다
- `05_order.py`의 확인 단계를 지우면 확인 없이 전송됩니다
- 앱키와 시크릿을 저장소에 커밋하지 말아 주세요
- [DISCLAIMER.md](DISCLAIMER.md)를 꼭 읽어 주세요

## 라이선스

MIT — [LICENSE](LICENSE)

문의는 개발자 포털을 이용해 주세요.
