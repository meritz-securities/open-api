# Windows 배포 준비 — 서명된 설치본은 베타 이후 제공

베타에서는 `.github/workflows/release-studio.yml` 이 Windows 러너에서 `build_native.py` 폴더형 빌드와 `verify_native.py` 자동 검사를 거쳐 서명하지 않은 `.zip` 을 사전 릴리스에 올립니다. 이 문서는 그 다음 단계인 **서명된 NSIS 설치본** 준비를 다룹니다.

Windows 전용 빌드·설치 소스를 준비하는 단계입니다. 이 문서는 Windows 설치파일이나 운영 승인 증거가 아닙니다. 실제 Windows x64 빌드, 의존성 고정·감사, 서명, 신규 사용자 설치·제거·업데이트 시험이 끝나야 설치 파일을 배포할 수 있습니다. macOS 실행 파일을 Windows 설치기에 넣거나 macOS의 Python 고정 목록을 Windows에 적용하지 않습니다.

## 설치 방식

NSIS 3 기반의 사용자별 검토용 설치 소스를 생성합니다. 관리자 권한·서비스·시작프로그램·방화벽 예외를 추가하지 않습니다. 설치 경로는 `%LOCALAPPDATA%\Programs\MeritzStrategyPlatformPreview\<bundle-id>`이며 빌드별로 분리합니다. 같은 빌드의 기존 경로가 있으면 덮어쓰지 않습니다. 업데이트는 새 빌드를 별도로 검토·설치하는 방식이며 자동 업데이트는 없습니다.

NSIS 공식 문서는 `RequestExecutionLevel user`를 사용자 권한 설치로, `SetShellVarContext current`를 현재 사용자 바로가기 경로로 정의합니다. NSIS의 기본 코드 라이선스는 zlib/libpng이며 예외 모듈은 별도 조건이 있습니다. 설치 컴파일러 자체와 동봉하는 플러그인의 실제 버전·라이선스는 Windows 빌드 환경에서 기록해야 합니다. 이 선택이 제품 전체의 라이선스 검토 완료를 의미하지 않습니다.

- [NSIS 명령·설치/제거 레퍼런스](https://nsis.sourceforge.io/Docs/Chapter4.html)
- [NSIS 공식 라이선스](https://nsis.sourceforge.io/Docs/AppendixI.html)

설치기는 기존 Claude/Codex MCP 설정을 읽거나 덮어쓰지 않습니다. Studio와 MCP는 같은 설치 EXE를 사용하며 MCP 실행에는 반드시 `--mcp`가 필요합니다. 기존 open-api-mcp 설치 안내의 다운로드 대상은 다른 제품이므로 재사용하지 않습니다. 고객 키·계좌·환경 파일을 설치 산출물에 포함하지 않습니다.

## Windows에서 만들어야 하는 실제 입력

1. Windows x64의 별도 Python 3.11 개발 환경과 검증한 PyInstaller를 준비합니다. 현재 `uv.lock`은 프로젝트 의존성의 플랫폼 조건을 담지만 Windows 빌드 도구를 포함한 전체 고정 환경·아티팩트 감사는 미완료입니다.
2. 해당 환경에서 Studio를 빌드하고 `packaging/build_native.py`를 실행합니다. 현재 Windows 빌드의 의존성 표시는 `inventory only; target-platform validation required`이며 이를 배포 승인으로 바꾸지 않습니다.
3. 실제 `meritz-strategy-platform.exe`와 `_internal`을 포함한 전체 onedir, `build-manifest.json`을 확보합니다. 고객용 onedir에는 개발 manifest를 복사하지 않습니다.
4. 생성기에 실제 Windows manifest와 bundle 경로를 전달합니다. OS·아키텍처·PE 헤더·파일 목록·해시·경로 검사를 통과한 파일만 새 준비 폴더에 복사합니다. 구조 검사 통과는 Windows 실행 성공을 의미하지 않습니다.

```powershell
& C:\build-env\Scripts\python.exe packaging\build_native.py --source-root C:\source --output-root C:\builds\new-windows-build
& C:\build-env\Scripts\python.exe packaging\windows\prepare_installer.py --manifest C:\builds\new-windows-build\build-manifest.json --bundle C:\builds\new-windows-build\dist\meritz-strategy-platform --output C:\builds\new-installer-source
```

`installer.nsi`, 검증된 `payload`, 준비 manifest는 설치기 소스입니다. `PREPARED_UNTESTED` 상태이며 컴파일된 설치파일이 아닙니다. 컴파일 직전에 아래 명령으로 준비 폴더의 최상위 목록, payload 목록·해시와 설치 스크립트의 해시·현재 생성기와의 일치를 다시 확인합니다. 최상위에는 installer.nsi, payload, preparation-manifest.json만 허용합니다. NSIS include는 컴파일러의 NSISDIR 아래 Include에서만 읽도록 생성합니다. 준비 manifest 자체는 서명된 신뢰 근거가 아니므로 준비 폴더를 신뢰할 수 있게 보관해야 합니다.

```powershell
& C:\build-env\Scripts\python.exe packaging\windows\prepare_installer.py --verify-prepared C:\builds\new-installer-source
```

기존 준비 소스가 생성기 변경으로 맞지 않으면 새 폴더에 다시 생성합니다. 검증이 성공한 후 Windows에서 검증한 NSIS 컴파일러로 소스를 컴파일하고 설치 EXE의 해시와 서명을 별도로 기록합니다. 설치 EXE 생성만으로 설치 검증을 완료 처리하지 않습니다.

## 실행·데이터·제거

Studio는 EXE를 인자 없이 실행합니다. 현재 콘솔 서버가 실행되는 동안 브라우저가 연결되므로 콘솔을 닫으면 Studio 연결도 종료됩니다. 고객에게 Python이나 Docker 설치를 요구하지 않지만 이 동작은 실제 Windows에서 확인해야 합니다.

전략과 결과의 기본 저장 위치는 `%LOCALAPPDATA%\MeritzStrategy`입니다. `MERITZ_STRATEGY_HOME`을 지정했다면 그 경로를 사용합니다. CLI의 `--root`는 별도 시험 데이터 위치를 지정합니다. Windows ACL 보호를 macOS의 chmod와 동일하다고 주장하지 않습니다.

제거는 설치 manifest에 들어간 프로그램 파일·전용 바로가기·해당 빌드의 제거 등록만 대상으로 합니다. 데이터 폴더·고객 키·MCP 설정·설치 후 사용자가 추가한 파일은 보존합니다. 디렉터리 재귀 삭제나 실행 중 프로세스 강제 종료를 하지 않습니다. 실행 중인 앱을 닫고 다시 제거해야 합니다. 앞선 제거가 일부 실패해 실행 파일이 이미 없어도 나머지 설치 파일의 제거를 재시도할 수 있도록 구성합니다. 잠김과 접근 오류는 파일 없음과 구분합니다. 제거 등록이 이미 없으면 64비트 HKCU에서 부재를 확인한 뒤 계속하며, 다른 레지스트리 오류는 중단합니다. 자체 제거 파일은 프로그램 파일·바로가기·등록 처리가 끝난 뒤 마지막에 삭제합니다. 목록에 포함된 프로그램 파일을 수정한 경우에는 제거 대상입니다. 보존 보장은 목록 밖의 고객 파일에 한정되며 실제 Windows 제거 시험도 필요합니다.

Windows 소스 실행에서는 사용자 Python 전략을 지원하지 않습니다. `build_native.py` 가 만든 번들은 WebAssembly(WASI) Python 실행기를 동봉하므로 Python 전략을 격리 실행하며, `verify_native.py --require-python` 이 그 결과를 검산합니다. 실행기가 없는 환경에서는 Python 을 거부해야 하고, 설치 성공을 위해 격리 검사를 비활성화하거나 macOS worker를 Windows에서 실행하도록 변경하지 않습니다.

## 설치 파일 배포 전 시험 항목

| 시험 | 합격 근거 | 시점 |
|---|---|---|
| 네이티브 빌드·고정 의존성 | Windows 전용 환경 목록·잠금·감사·빌드 manifest·파일 hash | 베타 이후 |
| 설치 권한 | 관리자 승격 없이 새 사용자 설치, 현재 사용자 경로·등록만 생성 | 베타 이후 |
| Python/Docker 없는 고객 환경 | 설치 앱의 DSL 실행 및 결과 저장 | 베타 이후 |
| 공통 Runtime | CLI·MCP·Studio의 동일 입력 결과 hash 일치 | 베타 이후 |
| Python 전략 격리 실행 | 동봉 WASI 실행기로 합성 데이터 결과 검산, 격리 위반(파일 쓰기) 거부, 실행기 부재 시 unsupported_platform 거부 | 베타 이후 |
| 로컬 보안 | Host/Origin·티켓 재사용 거부·허용되지 않은 파일 경로 거부 | 베타 이후 |
| 파일 잠금·중단 복구 | 중복 실행 제어, 프로세스 종료 후 interrupted 기록·계보 유지 | 베타 이후 |
| 콘솔·브라우저 | 기본 브라우저 연결, 종료·재실행, 네트워크 요청 경계 | 베타 이후 |
| 업데이트 | 실행 중 버전 보존, 새 버전 별도 설치, 기존 데이터 재조회 | 베타 이후 |
| 제거 | 설치 파일만 삭제, 고객 데이터·추가 파일·MCP 설정 보존 | 베타 이후 |
| 서명·무결성 | 코드 서명·타임스탬프·해시·배포 경로 확인 | 베타 이후 |

검증하지 못한 항목은 SKIP/PENDING으로 유지합니다. macOS 단위시험이나 가짜 PE 헤더 fixture를 위 Windows 시험의 합격 근거로 사용하지 않습니다.

## Windows 네이티브 실행 시험 스크립트

`packaging/windows/verify_native.py`는 Windows에서만 실행합니다. 실제 Windows onedir와 원래 build manifest를 대상으로 하며 설치 전 bundle을 검사합니다. 설치 경로에는 uninstaller가 추가되므로 이 검사를 그대로 설치 후 경로에 적용하지 않습니다.

```powershell
& C:\build-env\Scripts\python.exe packaging\windows\verify_native.py --manifest C:\builds\new-windows-build\build-manifest.json --bundle C:\builds\new-windows-build\dist\meritz-strategy-platform --output C:\evidence\windows-native-smoke.json
```

시험 담당자의 개발 환경에는 FastMCP client가 필요하지만 시험 대상 자식 EXE의 PATH에서는 Python을 제외합니다. 고객에게 Python을 요구하는 설계가 아닙니다. 전용 임시 데이터를 사용해 합성 OHLC의 독립 기대값·CLI 재실행·MCP 결과 일치·저장 결과·Python 전략 격리 실행(`--require-python`)·로컬 HTTP Origin/티켓을 검사합니다. 주문과 실제 시세를 호출하지 않으며 고객 환경변수나 .env를 증적에 포함하지 않습니다. 로컬 HTTP 요청은 환경변수·시스템 프록시를 사용하지 않습니다. 증적에는 bundle 대상 아키텍처와 호스트가 보고한 아키텍처·OS 버전을 구분해 기록하며, 실행기가 없는 번들의 Python 거부는 MCP의 명시적인 unsupported_platform 사유까지 확인합니다. 브라우저 JavaScript·설치·서명·Windows ACL 검증을 대신하지 않습니다.
