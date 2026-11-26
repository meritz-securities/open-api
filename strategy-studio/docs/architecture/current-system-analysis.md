# 현재 시스템 분석

기준: 2026-11-12 시점의 기존 로컬 MCP 작업본. 설치된 고객 실행본은 변경하지 않는다. 분석은 실제 코드 읽기에 근거한다.

- `meritz/server.py:build_server`가 Registry·Catalog를 생성하고 기존 계좌/시세/주문/환전/참조 도구와 stdio 전용 백테스트 도구를 등록한다.
- 인증과 토큰은 기존 `client.TokenManager`, 계정 선택은 `accounts.Registry.resolve`가 담당한다. SDK examples와 MCP 모두 `meritz` namespace를 사용하므로 두 패키지의 단순 동시 설치를 금지한다.
- `strategy_engine.run_strategy`는 다음 봉 시가의 host-owned 회계·가상 체결·미래 봉 제외 observation을 구현한다. 기존 SMA용 `backtest_engine`에는 별도 회계 루프가 있어 공통 경로로 수렴할 대상이다.
- 임의 Python은 `strategy_runner.StrategySession`의 macOS sandbox-exec·9개 실행 전 probe·최소 env·시간/메모리/출력 제한으로 실행한다. Windows 격리는 미구현이며 안전하지 않은 fallback은 없다.
- 국내 시세 어댑터는 six-digit, TJ/Y, 수정주가 요청, 최대365일이다. 해외 일봉 endpoint는 명세에 있으나 기간 인자가 없고, 3년 범위 확보는 제공자 응답에 따른다.
- 결과는 stdio 프로세스의 OrderedDict 16개에만 저장된다. SQLite·전략저장·재시작 복원은 없다.
- 기존 웹 화면은 동일 MCP 도구를 loopback bridge로 호출한다. Host/Origin/token 검증과 제한된 도구 whitelist를 재사용할 수 있다.
- 기존 native는 macOS unsigned onedir 검증본이다. Windows 설치/임의 코드 격리/서명/공증/신규환경 검증은 미완료다.

## 이후 확인한 변경

위 목록은 분석 시점의 기존 MCP 작업본에 대한 것이며 현재 구현 상태가 아닙니다. 이후 공통 Runtime·SQLite·SDK·진단·실험·내보내기를 추가했습니다. 2026-11-12 수집한 현행 공개 일봉 명세와 동봉 카탈로그에는 국내·해외 모두 `from`/`to`가 있습니다. 이를 사용하는 미국 단일 요청 경로도 구현했지만 실제 제공 기간·완전성·고객 권한은 아직 검증하지 않았습니다. 현재 기능 범위는 [사용 안내](../user-guide/platform-local.md)와 [Windows v1 범위](../windows-v1-scope.md)를 기준으로 확인합니다.

## baseline 요구사항 대비 핵심 차이

공통 SDK, 선언형 DSL, 비주얼 빌더, 전략·데이터 영속화, 거래별 평단·실현손익·세금·지정가, Doctor·실험·OOS 검증·내보내기는 새 구현이 필요하다. 실제 지원 확인 없이 기존 examples를 SDK, 신호 집계를 백테스트 엔진, mock 데이터를 실제 고객 시세로 표현하지 않는다.
