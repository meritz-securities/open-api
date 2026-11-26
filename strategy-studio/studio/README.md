# Meritz Strategy Studio

고객 PC의 공통 Runtime에 연결하는 React + TypeScript 콘솔형 워크스페이스. 기본은 다크 테마이며 라이트 테마로 전환할 수 있습니다. 계산 엔진이나 시세 인증을 UI에 중복 구현하지 않습니다.

## 빌드 및 확인

```sh
npm ci
npm run build
npm test
```

`dist/index.html`은 외부 CDN 의존성 없는 단일 파일입니다. 플랫폼의 인증된 loopback 서버에서 제공합니다. Vite 단독 실행은 화면 개발용이며 Runtime을 제공하지 않습니다. 운영 실행은 MCP의 Studio 열기 또는 데스크톱 실행기를 사용합니다.

## 인증 계약

실행기가 `/#ticket=...` 주소를 엽니다. UI는 fragment를 즉시 지우고 `POST /api/session {ticket}`로 1회용 티켓을 교환합니다. 토큰은 메모리에만 유지하고 `/api/platform` 호출에 `X-Platform-Token`을 전달합니다. 정적 HTML·localStorage에 토큰을 기록하지 않습니다. 새로고침은 실행기에서 다시 여는 방식입니다. 일반 주소나 실패한 서버 응답을 합성 결과로 대체하지 않습니다.

## 구현 범위

- 실제 DSL 조건 편집·AND/OR·노드 위치 드래그, Python SDK 코드 입력과 파일 가져오기
- Runtime 검증·불변 리비전 저장, CSV/JSON 데이터 가져오기, 명시적 합성 시세 생성
- 공통 Runtime 실행, 성과·거래 근거·위험 진단·규칙 기반 후보 재실행과 비교
- 요약/차트 분석/거래 검증 3가지 리포트 배치, 국내 상승 빨강/하락 파랑, 반응형·다크 모드
- Runtime HTML/JSON/CSV 내보내기. PDF는 브라우저 인쇄 기능이며 전용 PDF 엔진이 아닙니다.
- 강건성 분석의 가정과 사후 기간 분리 한계 표시

Studio 직접 LLM 연결은 구성되지 않았습니다. Claude 요청 복사는 실제 AI 실행으로 표시하지 않습니다. 규칙 기반 개선 후보도 AI 생성으로 표시하지 않습니다. 메리츠 실데이터 확보는 기존 MCP의 고객 인증과 Runtime 연결을 통해 수행하며, UI에 키를 복제하지 않습니다.

## UI 기술 선택

DSL은 접근 가능한 조건 편집과 실제 정의를 생성하는 작은 노드 편집기를 유지합니다. Python은 Monaco Editor 0.57.0 ESM과 Python 토큰화를 단일 HTML에 포함합니다. CDN, 원격 worker, eval 또는 Python 언어 서버를 사용하지 않습니다. worker가 필요한 단어 기반 자동완성 등은 비활성화하고 SDK Context 메서드 자동완성·hover 도움말을 로컬 provider로 제공합니다. SDK 도움말은 `sdk/meritz_strategy/__init__.py`를 기준으로 하며 전체 Python 타입 추론이 아닙니다. 범용 Python 코드의 DSL 양방향 변환은 하지 않습니다.

`전략 검증`은 인증된 Runtime의 실제 검증 결과를 표시합니다. 위치가 있는 오류만 에디터에 표시하며, 위치 없는 오류도 검증 결과에 남습니다. Runtime의 1-based Unicode 코드 포인트 열을 Monaco UTF-16 열로 변환합니다. 편집 시 이전 오류를 지우고 다른 정의에 대한 늦은 검증 결과는 적용하지 않습니다. 처리 중 에디터는 읽기 전용입니다. Esc 다음 Tab으로 에디터를 빠져나올 수 있습니다.

공식 npm registry의 정확한 버전을 lockfile에 고정했습니다. Monaco의 DOMPurify 의존성은 보안 수정판 3.4.17로 override합니다. 의존성 변경 때 `npm audit`와 실제 Runtime 브라우저 검사를 다시 수행해야 합니다. 라이선스는 Monaco MIT 및 DOMPurify의 upstream 라이선스를 따릅니다.

## 검증 범위

Playwright의 계약 fixture는 테스트 안에서만 생성합니다. 인증 교환, fragment 제거, 검증·저장·데이터·실행·리포트·후보 재계산·검증, 선택한 거래 유지, 320px overflow, 비인증 잠금을 검증합니다. 실제 Runtime 및 OS 설치 검증은 별도 통합 품질 게이트입니다. 테스트 fixture 통과를 실데이터 검증으로 해석하지 않습니다.

실제 Runtime + 배포 HTML 통합 검사는 아래처럼 실행합니다 (해당 Python에 프로젝트 의존성 필요).

```sh
npm run build
MERITZ_RUNTIME_ROOT=/path/to/meritz-strategy-platform MERITZ_TEST_PYTHON=/path/to/python npm test
```

`tests/runtime.spec.ts`는 route mock 없이 실제 인증, DSL 실행 및 Monaco 코드 입력→문법 오류→수정→재검증, SDK 제안, 테마, 모바일 폭, keyboard exit, 재마운트를 검사합니다. 에디터 검사에서 외부 요청, 브라우저 오류·경고가 없으며 Runtime의 hash script CSP가 유지되는지 확인합니다. 라이브 시세나 실주문을 호출하지 않습니다.

SDK 제안은 `from meritz_strategy import Context` (또는 `as` 별칭)와 같은 함수 안의 `변수 = Context(...)` 단순 대입을 확인한 변수에만 제공합니다. 문자열·주석·일반 dict에는 제안하지 않습니다. 복잡한 제어 흐름, 멤버 저장, 재대입 별칭의 타입을 추론하지 않으므로 일부 유효한 Python에도 제안이 없을 수 있습니다. 매수·매도 자동완성의 `quantity` 자리표시자는 사용자가 수량으로 바꿔야 합니다.

worker 없는 구성에서는 Unicode 혼동 문자·보이지 않는 문자 강조도 비활성화합니다. 해당 문자 검사, 전체 Python 의미 분석이나 보안 검토를 편집기가 수행한다고 해석해서는 안 됩니다. 실제 지원 규칙은 Runtime 검증 결과를 확인하세요.
