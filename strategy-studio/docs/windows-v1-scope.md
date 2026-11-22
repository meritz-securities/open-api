# Windows 지원 범위 — 템플릿·비주얼 빌더 우선

베타의 Windows 범위는 **예제 전략 + 비주얼 빌더(DSL) + CSV 가져오기 + 리포트** 입니다.
직접 작성한 Python 전략은 Windows 동봉 실행기(WASI)가 들어 있는 패키징 번들에서만 실행됩니다.
소스로 실행하는 베타에서는 Windows 에서 Python 코드 작성 방식이 비활성화되고, 비주얼 빌더 전략만 사용합니다.
[베타 범위와 알려진 제한](customer-readiness.md)을 함께 보십시오.

## 1. 포함 / 제외

`runtime/service.py` 의 `capabilities()` 가 `pythonStrategy`(Windows 동봉 실행기 포함 여부), `dsl = true`, `aiStudio = false` 를 알립니다.

| 기능 | Windows | 비고 |
|---|---|---|
| 예제 전략 35종 선택·파라미터 | 포함 | DSL(`kind:"dsl"`), 플랫폼 가드 없음 |
| 비주얼 빌더 — 직접 편집은 SMA·EMA·RSI(+가격·숫자) 피연산자, 조건·AND/OR 조합·손절익절 | 포함 | `studio/src/Builder.tsx` |
| 다른 지표(WMA·MACD·볼린저 등)를 쓰는 예제 27종 | 읽기 전용 실행 | 선택·저장·실행 가능, 조건 변경은 JSON 고급 편집 |
| CSV 가져오기 | 포함 | OHLC 최대 5,000봉 |
| 메리츠 시세 조회 | 지원 | 운영 Open API 앱키 필요 |
| 성과분석·리포트·JSON/CSV/HTML 내보내기 | 포함 | 플랫폼 무관 |
| 직접 작성한 Python 전략 실행 | 패키징 번들에서만 | 동봉 WASI 실행기에서 격리 검사 후 실행. 소스 실행 베타에서는 비활성 |
| Studio 안의 AI 기능 | 미제공(플랫폼 공통) | AI 활용은 MCP 경로 |

DSL 은 5,000봉, Python 은 1,000봉까지입니다.

## 2. Python 작성 방식의 플랫폼 게이팅

작성 방식 토글("비주얼 빌더" / "Python 코드")은 실행 환경에 따라 잠깁니다. Windows 에서 Python 경로로 가는 어떤 흐름도 내부 오류 코드를 그대로 보여 주지 않는 것이 기준입니다.

- Studio 는 저장소 준비 뒤 `capabilities().pythonStrategy` 를 읽습니다. 상태는 `unknown / supported / unsupported` 세 가지이며, `unknown` 에서도 Python 만 잠깁니다.
- `unsupported` 면 "Python 코드" 버튼이 비활성화되고 "Python 전략 격리 환경이 준비되지 않았습니다" 안내가 보입니다.
- 저장된 Python 전략을 열면 읽기 전용으로 표시하고 실행 버튼·`.py` 가져오기를 비활성화합니다.
- 자동 테스트: `studio/tests/python-platform-gate.spec.ts`.

## 3. 비주얼 빌더 확장 방향

Python 없이 되는 범위를 넓히는 것이 우선입니다. 다중 조건 조합 UX, 포지션 사이징 규칙, 시간·세션 필터, 지표 파라미터 스윕, 멀티 지표 템플릿 순서로 검토하고 있습니다. 베타 기간의 의견은 GitHub 이슈로 받습니다.

## 4. 빌드

- 개발용 빌드: `packaging/build_internal.py`(macOS·Windows 공용) 또는 `packaging/windows/build_internal_windows.ps1`
- 정식 빌드: `packaging/build_native.py` → `packaging/windows/prepare_installer.py --manifest --bundle --output`(NSIS). 베타에서는 서명하지 않은 폴더형 `.zip`(`Start.cmd`)을 릴리스에 올리고, 서명된 설치 파일(NSIS)은 베타 이후에 배포합니다.

## 5. 안내 문구

- 소개: "Windows 버전은 예제 전략과 비주얼 빌더(DSL) 전략의 백테스트를 지원합니다. 직접 작성한 Python 전략은 동봉 실행기가 있는 번들에서 실행합니다."
- 빌더: "직접 조건 편집은 SMA·EMA·RSI 지표를 지원합니다. 다른 지표를 쓰는 예제는 그대로 실행할 수 있으며 조건 변경은 JSON 고급 편집에서 합니다."
- 시세: "시세 조회에는 운영 메리츠 Open API 앱키가 필요합니다. 모든 체결은 가상이며 실주문이 아닙니다."
- AI: "Studio 는 상용 AI 서비스에 연결하지 않습니다. AI 활용은 메리츠 MCP(`--mcp`)에 AI 호스트를 연결해 사용합니다."

## 6. AI 기능 범위 (플랫폼 공통)

- 고객용 Studio 는 상용 AI 에 직접 연결하지 않습니다. 별도 AI API 키 입력을 요구하지 않으며, AI 초안 생성·AI 개선 요청 패널은 노출하지 않습니다.
- 구현: `runtime.server.main()` 이 `create_server(ai_studio=False)` 로 기동합니다. `capabilities.aiStudio=false` 이고, 모델 연결·공급자 호출 액션은 서버에서 403 `AI_STUDIO_DISABLED` 로 거부합니다. UI 는 `aiStudio===true` 일 때만 AI 패널을 렌더합니다.
- AI 활용은 `--mcp` 로 연결한 AI 호스트가 Open API 와 전략 Runtime 도구를 호출하는 경로만 제공합니다. 이 경로에서 Studio 는 고객 앱키를 받지 않습니다.
- 테스트: `studio/tests/ai-studio-hidden.spec.ts`, `tests/test_ai_studio_disabled.py`.
