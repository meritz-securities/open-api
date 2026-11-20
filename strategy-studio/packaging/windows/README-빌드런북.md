# Windows 빌드 런북 (내부 테스트 / 정식 배포)

맥에서는 Windows 실행파일을 만들 수 없다(PyInstaller 크로스컴파일 불가). **아래는 Windows PC에서** 실행한다.

## ⚠️ 먼저 알 것 — Windows 기능 공백
`docs/release/windows-execution-gap.md` 참조. Windows에서:
- ✅ **동작**: 템플릿(DSL) 백테스트, CSV 가져오기, 성과분석/리포트, studio UI
- 직접 작성한 Python 전략에는 동봉 WASI 실행기를 사용한다. 설치본 검증 시 `verify_native.py`에 `--require-python`을 추가해 CLI와 Studio HTTP의 계산 결과를 확인한다.
- AI 개선 흐름은 별도 범위다. 설치 파일 배포 전 시험 항목은 docs/release/windows-installation.md 를 따른다.

## A. 내부 테스트 번들 (빠름, 미서명)
맥에서 검증한 것과 동일 구성. Apple Silicon 맥 빌드와 1:1 대응.
```powershell
cd <open-api 저장소>\strategy-studio
uv venv .win-build
.\.win-build\Scripts\python -m pip install -e ".[dev]" pyinstaller tzdata pywebview
powershell -ExecutionPolicy Bypass -File packaging\windows\build_internal_windows.ps1
# pywebview = 자체 창(브라우저 미사용). 미설치면 브라우저 모드로 빌드. 실행 PC엔 WebView2 런타임 필요(Win10/11 대부분 기본).
# -> %TEMP%\mstp-internal-win\dist\meritz-strategy-platform\Start.cmd 로 실행
```
실행 → 브라우저에 Strategy Studio. 템플릿 35개·import 백테스트 확인.

## B. 정식(정본) 배포본 — 서명/공증 전 단계
```powershell
# 1) 핀 환경으로 의존성 설치 (packaging\windows\*-build-requirements 가 있으면 그대로)
# 2) 네이티브 onedir 빌드 (Windows에선 핀 게이트가 걸리지 않음)
python packaging\build_native.py --output-root <비어있는 새 폴더>
#    -> <출력 폴더>\dist\meritz-strategy-platform\ (Start.cmd 포함) + <출력 폴더>\build-manifest.json (windowsTested:false)
# 3) 설치본(NSIS) 소스 준비 → Windows에서 NSIS 3 로 컴파일. --manifest/--bundle/--output 세 인자 모두 필수.
python packaging\windows\prepare_installer.py --manifest <출력 폴더>\build-manifest.json --bundle <출력 폴더>\dist\meritz-strategy-platform --output <비어있는 설치 소스 폴더>
#    준비 폴더 무결성만 다시 확인: python packaging\windows\prepare_installer.py --verify-prepared <설치 소스 폴더>
# 4) 번들 검사 (설치 전 onedir 대상)
python packaging\windows\verify_native.py --manifest <출력 폴더>\build-manifest.json --bundle <출력 폴더>\dist\meritz-strategy-platform --output <증적 json>
```
**고객 배포 전 Authenticode 코드사이닝(조직 인증서, 타임스탬프 포함)이 필수**다. 서명·배포는 별도 수행하며, 미서명 EXE/설치본은 내부 테스트 용도로만 쓴다.

## C. 자동화
공개 저장소에는 빌드·릴리스 워크플로가 없다. 위 A·B 는 빌드 담당자가 수동으로 수행하며, 베타 기간에는 설치 파일·실행 파일을 배포하지 않는다.

> 이 문서의 A/B 산출물은 서명 단계 전까지 **미서명**이다. 내부 테스트에서만 macOS는 우클릭"열기", Windows는 SmartScreen "추가 정보→실행"로 1회 허용한다. **고객 배포 전 Authenticode 서명 필수** — SmartScreen 우회 안내를 고객에게 배포 절차로 제공하지 않는다. 라이브 시세는 **운영 Open API 키** 필요(앱이 운영 도메인 고정), 개발/내부 데이터는 import 경로.
