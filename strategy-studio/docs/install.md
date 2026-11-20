# 설치

[README](../README.md) 의 설치 표로 대부분 끝납니다. 이 문서는 막힐 때 보는 절차입니다 —
OS 보안 경고, 기동 대기, 체크섬 대조, 설정 파일 직접 편집.

앱키·시크릿 발급과 API 권한 신청은 [개발자 포털](https://openapi.imeritz.com)에서 합니다.

## Claude Desktop — `.mcpb` 더블클릭

| 플랫폼 | 파일 |
|---|---|
| macOS · Apple 실리콘 | [meritz-open-api-darwin-arm64.mcpb](https://github.com/meritz-securities/open-api-mcp/releases/latest/download/meritz-open-api-darwin-arm64.mcpb) |
| macOS · Intel | [meritz-open-api-darwin-x64.mcpb](https://github.com/meritz-securities/open-api-mcp/releases/latest/download/meritz-open-api-darwin-x64.mcpb) |
| Windows | [meritz-open-api-win32-x64.mcpb](https://github.com/meritz-securities/open-api-mcp/releases/latest/download/meritz-open-api-win32-x64.mcpb) |

더블클릭하면 Claude Desktop 이 앱키·시크릿을 묻고, 값은 OS 키체인(macOS) 또는 자격 증명
관리자(Windows)에 저장됩니다. 조회 전용 스위치는 기본값이 켜짐입니다.

이 파일에는 코드서명이 없어 macOS 는 "확인되지 않은 개발자" 경고를 띄웁니다. 넘기기 전에
아래 "체크섬 대조"로 파일을 확인하십시오. 값이 같으면 시스템 설정 → 개인정보 보호 및 보안 →
그래도 열기로 진행합니다.

새 버전은 파일을 다시 받아 설치합니다. 자동 갱신되지 않습니다.

## `install.py` — 실행 파일 내려받기와 등록

```bash
git clone https://github.com/meritz-securities/open-api-mcp.git
cd open-api-mcp
python3 install.py                            # 내려받기 + 체크섬 대조 + 연결 방법 출력
python3 install.py --register claude-code     # codex · gemini 도 됩니다
```

하는 일: 이 PC 에 맞는 실행 파일을 릴리스에서 받아 `bin/` 에 두고, 릴리스의
`SHA256SUMS.txt` 와 대조하고, 실행 권한과 macOS 격리 속성 해제를 적용합니다.
Python 은 이 스크립트를 돌리는 데만 쓰입니다 — 실행 파일 자체는 Python 없이 동작합니다.

`--register` 는 앱키·시크릿을 화면에 찍지 않고 입력받아 클라이언트에 등록합니다.
같은 일을 `claude mcp add --env …` 로 직접 하면 앱키가 셸 기록 파일에 남습니다.

## 실행 파일을 직접 등록

| 플랫폼 | 파일 |
|---|---|
| macOS · Apple 실리콘 | [meritz-mcp-darwin-arm64](https://github.com/meritz-securities/open-api-mcp/releases/latest/download/meritz-mcp-darwin-arm64) |
| macOS · Intel | [meritz-mcp-darwin-x64](https://github.com/meritz-securities/open-api-mcp/releases/latest/download/meritz-mcp-darwin-x64) |
| Windows | [meritz-mcp-win32-x64.exe](https://github.com/meritz-securities/open-api-mcp/releases/latest/download/meritz-mcp-win32-x64.exe) |

macOS 에서는 실행 권한과 격리 속성 해제를 둘 다 해야 합니다.

```bash
chmod +x ~/Downloads/meritz-mcp-darwin-arm64
xattr -d com.apple.quarantine ~/Downloads/meritz-mcp-darwin-arm64
mkdir -p ~/meritz && mv ~/Downloads/meritz-mcp-darwin-arm64 ~/meritz/meritz-mcp
```

> **격리 속성을 지우지 않으면 서버가 뜨지 않습니다.** 브라우저로 받은 파일에는 macOS 가
> `com.apple.quarantine` 을 붙입니다. 이 실행 파일은 ad-hoc 서명이라 Apple 개발자 인증서가
> 없고 공증도 되어 있지 않아, 속성이 붙은 채로 실행되면 **아무 메시지 없이 종료코드
> -9(SIGKILL)로 죽습니다.** AI 클라이언트의 도구 목록에 메리츠가 보이지 않으면 이 속성이
> 남아 있을 가능성이 큽니다. 속성만 지우면 같은 파일이 정상 동작합니다.
> `install.py` 는 이 두 단계를 대신 합니다. Claude Desktop 의 `.mcpb` 는 Claude Desktop 이
> 직접 풀기 때문에 속성이 붙지 않아 해당되지 않습니다.

> **Windows** — `.exe` 첫 실행에서 SmartScreen 경고가 뜹니다. 코드서명이 없어 표시되는
> 경고입니다. 넘기기 전에 체크섬을 대조하고, 경고 창의 추가 정보 → 실행으로 진행합니다.

등록 명령. `<경로>` 는 실행 파일의 전체 경로입니다.

| 클라이언트 | 명령 |
|---|---|
| Claude Code | `claude mcp add meritz --env MERITZ_APP_KEY=… --env MERITZ_APP_SECRET=… -- <경로>` |
| Codex CLI | `codex mcp add meritz --env MERITZ_APP_KEY=… --env MERITZ_APP_SECRET=… -- <경로>` |
| Gemini CLI | `gemini mcp add -e MERITZ_APP_KEY=… -e MERITZ_APP_SECRET=… meritz <경로>` |
| ChatGPT 데스크톱 | `python3 install.py --register codex` — Codex CLI 와 같은 설정 파일을 쓰므로 이 한 번으로 등록됩니다. 등록 뒤 앱을 재시작하십시오 |
| Cursor · VS Code | [`connect.html`](connect.html) 을 내려받아 브라우저로 열면 추가 버튼이 만들어집니다 |

이 명령들은 앱키를 명령줄에 둡니다. 셸 기록에 남기지 않으려면 `install.py --register` 를
쓰거나, 등록 후 셸 기록에서 해당 줄을 지우십시오.

> **기동 대기를 60초로 늘리십시오.** 실행 파일은 매 기동마다 자기 내용을 임시 폴더로 풀기
> 때문에 첫 응답까지 10초 안팎이 걸릴 수 있습니다. Codex CLI 와
> ChatGPT 데스크톱의 기본 대기는 10초라 여유가 없어, 느린 PC 나 백신 검사가 끼면 넘겨 도구가
> 아예 뜨지 않습니다. `~/.codex/config.toml` 의 `[mcp_servers.meritz]` 테이블에
> `startup_timeout_sec = 60` 을 넣으십시오.

> ChatGPT 데스크톱 앱·Codex CLI·IDE 확장은 `~/.codex/config.toml` 을 함께 씁니다. 한 곳에
> 등록하면 나머지에도 잡힙니다. ChatGPT 웹은 이 파일을 읽지 못합니다.

> [`connect.html`](connect.html) 은 앱키·실행 파일 경로를 넣으면 위 명령과 JSON 설정,
> Cursor·VS Code 추가 버튼을 만들어 주는 정적 페이지입니다. 입력값은 페이지 밖으로 나가지
> 않습니다. GitHub 에서 링크를 누르면 HTML 소스가 보이므로, 파일을 내려받아 브라우저로 여십시오.

## 설정 파일을 직접 편집

Codex CLI — `~/.codex/config.toml`

```toml
[mcp_servers.meritz]
command = "/Users/hong/meritz/meritz-mcp"
startup_timeout_sec = 60

[mcp_servers.meritz.env]
MERITZ_APP_KEY = "…"
MERITZ_APP_SECRET = "…"
MERITZ_READ_ONLY = "1"
```

`startup_timeout_sec` 는 `[mcp_servers.meritz.env]` 줄보다 **위**에 두십시오. 아래에 두면
환경변수로 읽힙니다. 환경변수는 반드시 `[mcp_servers.meritz.env]` 테이블로 적으십시오.
`env_vars = { … }` 처럼 인라인 테이블로 적으면 `invalid type: map, expected a sequence` 로
`config.toml` 전체가 읽히지 않아 Codex CLI 와 ChatGPT 데스크톱이 모두 뜨지 않습니다.

그 밖의 클라이언트 — Claude Desktop `claude_desktop_config.json`, Cursor `mcp.json`,
Gemini CLI `settings.json`

```json
{
  "mcpServers": {
    "meritz": {
      "command": "/Users/hong/meritz/meritz-mcp",
      "env": {
        "MERITZ_APP_KEY": "…",
        "MERITZ_APP_SECRET": "…",
        "MERITZ_READ_ONLY": "1"
      }
    }
  }
}
```

이 파일들에는 앱키가 평문으로 들어갑니다. 파일 권한과 백업·동기화 대상 여부를 확인하십시오.

uv 와 Python 3.11 이상이 이미 있으면 내려받기 없이 소스에서 실행할 수 있습니다.

```bash
claude mcp add meritz -- uvx --from git+https://github.com/meritz-securities/open-api-mcp meritz-mcp
codex  mcp add meritz -- uvx --from git+https://github.com/meritz-securities/open-api-mcp meritz-mcp
```

## 릴리스 자산

[릴리스 페이지](https://github.com/meritz-securities/open-api-mcp/releases/latest)에 열
개가 올라옵니다. 쓰는 것 하나만 받으면 됩니다.

| 파일 | 용도 |
|---|---|
| `meritz-open-api-{darwin-arm64,darwin-x64,win32-x64}.mcpb` | Claude Desktop 설치 파일 (30MB 안팎) |
| `meritz-mcp-{darwin-arm64,darwin-x64}` · `meritz-mcp-win32-x64.exe` | 실행 파일. 다른 클라이언트에 경로로 등록 (30MB 안팎) |
| `THIRD-PARTY-NOTICES-{darwin-arm64,darwin-x64,win32-x64}.txt` | 실행 파일에 들어간 오픈소스의 라이선스 전문 |
| `SHA256SUMS.txt` | 위 아홉 개의 체크섬 |

## 체크섬 대조

`SHA256SUMS.txt` 를 받은 파일과 같은 폴더에 두고 대조합니다.

```bash
shasum -a 256 --ignore-missing -c SHA256SUMS.txt
```

```powershell
Get-FileHash .\meritz-mcp-win32-x64.exe -Algorithm SHA256
```

`install.py` 로 설치하면 이 대조를 자동으로 하고, 값이 다르면 받은 파일을 지우고 중단합니다.

## Claude 웹·모바일, ChatGPT 웹 — 조회 전용 터널

Claude 웹·모바일과 ChatGPT 웹은 HTTPS 주소로 접근되는 MCP 서버만 받습니다. 이 서버를 HTTP
모드로 띄우고 터널로 임시 주소를 만들면 붙일 수 있습니다.

> **이 구성은 조회 전용으로만 쓰십시오.** 두 서비스 모두 커스텀 커넥터의 인증 옵션이 OAuth
> 아니면 없음뿐이라, 주소의 난수 경로가 사실상 유일한 접근 통제입니다. 그 주소는 연결한 AI
> 서비스 쪽에 커넥터 설정으로 저장되며 이용자가 지우기 전까지 남습니다. 주소를 알게 된
> 사람은 그 계좌의 잔고·보유종목을 조회할 수 있습니다.
> **주문·환전을 쓰려면 터널이 아니라 위의 실행 파일 등록 방식을 쓰십시오.**

ChatGPT 데스크톱 앱은 터널이 필요 없습니다. 실행 파일 등록 방식을 쓰십시오.

1. 앱키를 파일에 둡니다. 명령줄에 쓰면 셸 기록과 프로세스 목록에 남습니다.

   ```bash
   mkdir -p ~/meritz && touch ~/meritz/.env && chmod 600 ~/meritz/.env
   ```

   `~/meritz/.env` 내용:

   ```
   MERITZ_APP_KEY=발급받은_앱키
   MERITZ_APP_SECRET=발급받은_시크릿
   ```

2. 서버를 HTTP 모드로 띄웁니다. `<난수>` 는 추측할 수 없는 긴 문자열입니다
   (`openssl rand -hex 16`, 또는 [`connect.html`](connect.html) 이 만들어 줍니다).

   ```bash
   set -a; . ~/meritz/.env; set +a
   MERITZ_READ_ONLY=1 MCP_TYPE=streamable-http MCP_PORT=8765 MCP_PATH=/mcp/<난수> ~/meritz/meritz-mcp
   ```

   서버는 `127.0.0.1` 에만 바인딩합니다. 포트를 공유기에서 직접 열어 주지 마십시오.
   이 절의 명령은 bash·zsh 기준입니다.

3. 다른 터미널에서 터널을 엽니다.

   ```bash
   cloudflared tunnel --url http://127.0.0.1:8765     # 계정 없이 임시 주소
   ngrok http 8765                                     # ngrok 계정 필요
   ```

4. 커넥터에 `https://<터널 주소>/mcp/<난수>` 를 넣습니다. 인증은 비워 둡니다.
   Claude 는 설정 → 커넥터 → 커스텀 커넥터 추가, ChatGPT 웹은 설정 → 커넥터 → 고급 →
   개발자 모드 → 만들기입니다.

쓰고 나면 터널과 서버를 닫고, 커넥터 설정에서 그 주소를 지우십시오. 다시 열면 주소가
바뀌므로 커넥터도 다시 등록해야 합니다.
