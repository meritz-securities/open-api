# Standalone 앱의 메모리 전용 메리츠 연결

상태: HTTP 연결 backend 구현. 실제 고객 키·인증·시세 호출과 OS credential store 연동은 검증하지 않았습니다. 일반 고객에게 Python/Docker 설치를 요구하지 않는 native Runtime의 기존 시세 어댑터를 재사용합니다. UI 연결폼은 별도 구현·검토 대상입니다.

## 시작 경로

`runtime.server.main()`은 `create_server(allow_market_connection=True)`로 메모리 전용 연결을 명시적으로 켭니다. 이 경로는 Registry의 환경변수/.env 자동 탐색을 하지 않습니다. 처음에는 미설정이며 연결폼에서 고객이 직접 설정합니다. 기존 standalone 환경키를 조용히 승계하지 않습니다.

기본 `StrategyRuntime` 및 `create_server`는 opt-in하지 않습니다. `meritz_studio_open`이 전달한 기존 Runtime/Registry는 그대로 유지하고 이 화면에서 변경하지 못합니다. `allow_market_connection=True`와 기존 Registry를 동시에 주입하면 거부합니다. 따라서 MCP 설정을 복사하거나 공유 Registry를 수정하지 않습니다.

## 인증된 HTTP 전용 액션

기존 `/api/platform`의 Host/Origin/세션 토큰 및 요청 크기 제한 아래에서만 다음 액션을 처리합니다. 일반 service.dispatch, CLI, MCP 도구에는 등록하지 않습니다.

| action | arguments | 결과 |
|---|---|---|
| get_market_connection | {} | 연결 상태 |
| connect_market_session | {appKey, appSecret} | 설정 후 연결 상태 |
| disconnect_market_session | {} | 해제 후 연결 상태 |

키와 시크릿은 공백·제어문자·잘못된 surrogate가 없는 각각 1~4096자 문자열입니다. 서버는 Secret 원문을 오류에 반영하지 않습니다. 추가 인자는 거부하며 base_url, 파일경로, 환경변수, 계좌번호, 임의 API를 받지 않습니다. 신규 `Settings`의 공식 기본 endpoint와 `read_only=True`를 사용하고 기존 설정을 환경에서 가져오지 않습니다. 단일 앱 전용 연결이며 계좌 라벨은 고정입니다.

응답은 `{managementAllowed, source, configured, authenticated, busy, lastRequest, notice}`입니다.

- source는 session 또는 inherited입니다. inherited 경로는 managementAllowed=false입니다.
- configured는 키·시크릿 구성이 존재한다는 뜻입니다. authenticated는 항상 null로, 이 API가 별도의 인증 검증을 하지 않음을 나타냅니다.
- lastRequest는 null 또는 `{status: succeeded|failed, finishedAt, errorCode}`입니다. 로컬 입력 검사·응답 검증·snapshot 저장을 포함한 최근 fetch_data 전체 처리 결과만 나타내며 현재의 영구 인증·전체 시세 권한을 인증하지 않습니다. 입력 오류만으로 네트워크 호출 없이 failed가 될 수 있으며, 이 상태는 인증 실패나 공급자 장애의 증거가 아닙니다. 실패코드는 MARKET_FETCH_FAILED로 고정하고 원시 공급자 오류를 복사하지 않습니다.
- inherited 경로의 lastRequest/busy는 이 컨트롤러가 관찰하지 않으므로 null/false입니다. 이 값을 다른 MCP 작업이 없다는 증거로 사용하지 않습니다.

연결 버튼은 Registry/ApiClient를 메모리에 구성할 뿐 토큰 발급이나 시세 조회를 하지 않습니다. 실제 네트워크는 사용자가 기존 시세 조회를 실행할 때만 발생합니다. 조회는 KR/US 어댑터의 기존 범위·명시적 거래소/시세 모드·snapshot 검증을 그대로 사용합니다. 성공 snapshot과 백테스트 결과에 자격증명을 추가하지 않습니다.

조회 시작부터 snapshot 저장 완료까지 선택 Registry 참조를 고정하고 busy를 유지합니다. 그동안 연결 교체·해제와 두 번째 조회를 거부합니다. 실패해도 finally에서 busy를 해제합니다. 연결을 교체하면 이전 조회 상태를 비웁니다. 서로 다른 연결의 결과를 같은 최근 성공으로 표시하지 않습니다.

## 비밀정보 수명과 UI 의무

키는 앱 전용 Settings에만 보관합니다. DB, 파일, .env, 환경변수, URL, CLI argv, 응답, 로그에 기록하지 않습니다. OS credential store나 다른 제품 설정을 읽지 않습니다. 연결 해제는 Registry 참조를 버리는 동작이며 토큰 폐기나 서버 로그아웃 API를 호출하지 않습니다.

UI는 password 입력을 사용하고 제출 직후 두 입력을 비워야 합니다. localStorage/sessionStorage/IndexedDB/URL/분석 이벤트에 저장하지 않아야 하며 응답에도 원문을 요구하지 않습니다. 메모리의 Python/브라우저 문자열을 완전히 소거한다고 보장하지 않습니다. 로컬 HTTP와 브라우저 개발자 도구를 포함해 같은 OS 사용자에 대한 강한 비밀 격리를 제공하는 기능도 아닙니다.

브라우저 탭을 닫는 것은 백엔드 프로세스 종료나 연결 해제가 아닙니다. 사용자가 명시적으로 연결을 해제하거나 앱 프로세스가 종료될 때 세션 연결을 잃습니다. 재시작하면 다시 입력해야 합니다. 평문 .env 저장·다른 앱 자격증명 자동 복사·클립보드 전송을 편의 기능으로 추가하지 않습니다.

## 검증 범위

`tests/test_market_connection.py`는 실제 로컬 HTTP 세션/Origin 검사와 fake ApiClient를 사용합니다. 실제 키·토큰·공급자 네트워크 없이 미설정/설정 상태, 키 비저장, endpoint 고정, 공유 Registry 불변, 조회 중 교체 차단, 실패 후 회복, 동일 snapshot hash의 실제 엔진 실행을 확인합니다. 일반 Runtime/MCP·국내·미국 시세 기존 시험도 함께 실행합니다.

실제 고객 인증·시세 조회, UI 입력 수명, native 설치 경로의 end-to-end 연결, 안전한 CS 오류 분류 확대는 후속 검증 사항입니다. 신규 자연어 전략 생성이나 주문 기능을 이 연결 구현의 완료 범위로 포함하지 않습니다.
