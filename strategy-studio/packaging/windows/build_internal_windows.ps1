# 메리츠 전략 백테스트 — Windows 내부 테스트 빌드 (미서명·비release)
#
# 맥에서는 Windows 실행파일을 만들 수 없다(PyInstaller 크로스컴파일 불가). 이 스크립트는 Windows PC에서 실행한다.
# 빌드 인자를 여기서 중복 정의하지 않고 **공용 빌더 packaging/build_internal.py 를 그대로 호출**한다
# (맥 내부 빌드와 동일 구성: strategies·schema·studio/dist·indicators add-data, pywebview 설치 시 창 모드 수집 포함).
# 정식 배포본은 packaging/build_native.py + packaging/windows/prepare_installer.py (README-빌드런북.md 참조).
#
# Windows Python 전략은 동봉 WASI 실행기를 사용한다.
# [주의] 창 모드(pywebview)는 실행 PC에 WebView2 런타임이 필요하다(Win10/11 대부분 기본 탑재). 없으면 앱이 브라우저로 폴백한다.
#
# 사용:
#   cd <open-api 저장소>\strategy-studio
#   # 1) 고정된 의존성 설치: uv sync --project "$S" --locked --extra dev
#   # 2) 빌드:  powershell -ExecutionPolicy Bypass -File packaging\windows\build_internal_windows.ps1
$ErrorActionPreference = "Stop"
$S = (Resolve-Path "$PSScriptRoot\..\..").Path          # strategy-studio 폴더
$OUT = "$env:TEMP\mstp-internal-win"
$env:UV_PROJECT_ENVIRONMENT = "$S\.win-build"
uv sync --project "$S" --locked --extra dev
if ($LASTEXITCODE -ne 0) { throw "uv sync --locked failed (exit $LASTEXITCODE)" }
$PY = "$S\.win-build\Scripts\python.exe"

if ((Test-Path $OUT) -and (Get-ChildItem -Force $OUT | Select-Object -First 1)) { throw "Output directory must be empty; existing build is preserved: $OUT" }
New-Item -ItemType Directory -Force -Path $OUT | Out-Null

Write-Host "building via packaging/build_internal.py -> $OUT"
& $PY "$S\packaging\build_internal.py" --output-root $OUT
if ($LASTEXITCODE -ne 0) {
    Write-Error "build_internal.py failed (exit $LASTEXITCODE). 템플릿 수 불일치(35 기대)나 PyInstaller 오류를 위 로그에서 확인하세요."
    exit 1
}

$BUNDLE = "$OUT\dist\meritz-strategy-platform"
# build_internal.py 가 Windows 에서는 Start.cmd 를 이미 만든다. 산출물 최종 확인:
$n = (Get-ChildItem "$BUNDLE\_internal\strategies\*.json" -ErrorAction SilentlyContinue).Count
$hasWebviewJs = Test-Path "$BUNDLE\_internal\webview\js"
Write-Host "bundled strategy templates: $n (35 기대)"
Write-Host "webview assets (창 모드): $(if ($hasWebviewJs) {'포함'} else {'미포함 → 브라우저 모드 빌드'})"
if ($n -ne 35) { Write-Error "템플릿 번들 수 불일치: $n"; exit 1 }
Write-Host "DONE. 실행: $BUNDLE\Start.cmd  (또는 meritz-strategy-platform.exe)"
Write-Host "배포 zip: Compress-Archive -Path '$BUNDLE' -DestinationPath '$env:USERPROFILE\Downloads\메리츠_전략백테스트_내부테스트_win.zip'"
