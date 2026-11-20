# 검증 스크립트 (네트워크 없음)

엔진 산출을 독립 재계산과 대조한다. 입력은 `meritz.backtest_engine.demo_candles()`가 만드는 **결정적 합성 일봉 160봉**이며 실제 종목 시세가 아니다. 두 스크립트의 검사는 모두 항등식·정의 일치 검사이므로 입력 데이터에 고정된 기대값(특정 거래 수 등)이 없다.

- `scripts/precise_reconcile.py` — 체결역학(슬리피지·수수료·세금)·자금원장·성과지표(엔진 문서 정의)·결정성 대조
- `scripts/invariants_all35.py [--selftest]` — 35개 템플릿 불변식(회계 항등식·포트폴리오 정합·체결 인과성·마지막 봉 미체결·결정성). 템플릿 수가 35가 아니거나 체결 주문이 0건이면 실패로 처리한다. `--selftest`는 결함 주입 검출 확인

실행: `strategy-studio` 폴더에서 `MERITZ_NO_ENV_FILE=1 python docs/verification/scripts/<script>.py` (의존성은 같은 폴더에서 `uv sync` 또는 `pip install -e .`). 종료 코드 0이 통과다.
