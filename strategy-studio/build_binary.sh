#!/usr/bin/env bash
# 서버를 단일 실행 파일로 만듭니다 — 고객 PC 에 Python·uv 가 없어도 돕니다.
#
#   ./build_binary.sh            → bin/meritz-mcp       (Mac·Linux)
#   ./build_binary.sh            → bin/meritz-mcp.exe   (Windows, Git Bash)
#
# Claude Desktop 은 Node.js 만 내장하고 Python 은 내장하지 않습니다(2026-09 확인).
# 그래서 Python 번들은 고객이 uv 를 먼저 깔아야 뜨고, 이 실행 파일은 그 문턱을 없앱니다.
# 실행 파일에는 앱키를 넣지 않습니다. 앱키는 설치 화면·환경변수로 받습니다.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8   # Windows 콘솔(cp1252)에서 한글 출력이 깨지지 않게

NAME=meritz-mcp
OUT=bin
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) EXE=".exe" ;;
  *) EXE="" ;;
esac

command -v uv >/dev/null || { echo "uv 가 필요합니다 — https://docs.astral.sh/uv/"; exit 1; }

# 빌드 전용 가상환경. mcp[cli] 는 PyInstaller 가 mcp.cli 를 훑을 때 typer 를 찾기 때문에 넣는다.
# --clear 로 매번 새로 만든다. 남아 있는 환경에 덧씌우면 gen_notices.py 가
# 지금 빌드에 들어가지 않는 옛 배포판까지 고지문에 싣는다.
uv venv --quiet --clear .build-venv
# shellcheck disable=SC1091
if [ -f .build-venv/Scripts/activate ]; then . .build-venv/Scripts/activate; else . .build-venv/bin/activate; fi
# PyInstaller 는 버전을 고정한다 — 부트로더가 실행 파일에 그대로 들어가므로,
# 어느 버전의 부트로더를 배포했는지가 고지문(PyInstaller 의 COPYING.txt)과
# 맞아야 한다. 올릴 때는 gen_notices.py 로 고지문을 다시 뽑는다.
uv pip install --quiet "pyinstaller==6.22.3" "mcp[cli]" .

cat > .build-entry.py <<'PY'
from meritz.server import main
if __name__ == "__main__":
    main()
PY

# 실행 파일과 함께 배포되는 오픈소스의 라이선스 전문을 빌드 환경 기준으로 다시 뽑는다.
# MCP 릴리스에서는 이 파일이 별도 자산으로도 올라간다(open-api-mcp 저장소의 릴리스 워크플로).
python gen_notices.py > THIRD-PARTY-NOTICES.txt

rm -rf build "$OUT" ./*.spec
pyinstaller --onefile --name "$NAME" --distpath "$OUT" --workpath build \
  --collect-all fastmcp --collect-submodules mcp --collect-all pydantic --collect-all meritz \
  --log-level WARN .build-entry.py
rm -f .build-entry.py ./*.spec

echo
echo "만들어진 파일: $OUT/$NAME$EXE  ($(du -h "$OUT/$NAME$EXE" | cut -f1))"
# 자기 점검 — 빌드 venv 의 python 으로 실행하되, 자식 프로세스에는 python 없는 PATH 를 준다
#   (Windows Git Bash 에는 python3 이 없어 python 을 쓴다)
PY=$(command -v python3 || command -v python)
"$PY" - "$OUT/$NAME$EXE" <<'PY' || true
import json, subprocess, sys, os
env = {"PATH": "/usr/bin:/bin" if os.name != "nt" else os.environ.get("SystemRoot", "C:\\Windows") + "\\System32",
       "HOME": os.environ.get("HOME", ""), "SYSTEMROOT": os.environ.get("SystemRoot", ""), "MERITZ_APP_KEY": "x",
       "MERITZ_APP_SECRET": "y", "MERITZ_READ_ONLY": "1"}
msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "build-check", "version": "0"}}})
r = subprocess.run([sys.argv[1]], input=(msg + "\n").encode(), capture_output=True, timeout=60, env=env)
ok = b'"protocolVersion"' in r.stdout
print("자기 점검:", "통과 — initialize 응답" if ok else "실패\n" + r.stderr.decode(errors="replace")[-400:])
sys.exit(0 if ok else 1)
PY
