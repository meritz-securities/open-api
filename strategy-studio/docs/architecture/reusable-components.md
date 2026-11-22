# 재사용 목록

| 원천 | 활용 | 경계 |
|---|---|---|
| meritz/accounts.py, client.py, config.py | 기존 계정·인증·호출·오류 계약 | 키를 저장소/worker/보고서로 전달하지 않음 |
| meritz/catalog.py와 동봉 catalog | API 요청 명세 확인 | 카탈로그 존재는 실제3년 데이터 제공 증거 아님 |
| meritz/backtest_data.py | 국내 일봉 조회·검증 | 기존365일범위 유지, 임의 합성fallback 금지 |
| meritz/strategy_engine.py | 단일 가상 체결·회계 기준 | 새 UI/SDK/MCP에 별도 루프 금지 |
| meritz/strategy_runner.py, strategy_worker.py | Python 격리·IPC | macOS 지원, Windows failclosed |
| meritz/backtest_local.py | loopback HTTP 보안 구조 | 백테스트 whitelist 외 주문 도구 노출 금지 |
| ui/backtest | 기존 리포트/거래 상세/국내 색상 | 새 Studio는 동일 report 계약 소비 |
| 내부 SDK examples indicators/calc.py | 순수 지표 구현 후보 | 라이선스/정의/독립값 검증 후 사용 |

기존 MCP baseline은 파일 단위로 보존했고 인증/주문 코드를 새로 쓰지 않는다. 새 Python SDK는 `meritz_strategy` namespace를 사용한다. 검증되지 않은 오픈소스를 핵심 엔진으로 교체하지 않는다.
