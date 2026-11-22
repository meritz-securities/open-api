#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""메리츠증권 Open API MCP 설치 도우미.

포털 안내(git clone → python3 install.py)와 호환되도록 남겨 둔 파일입니다.
하는 일: 이 PC에 맞는 실행 파일을 GitHub 릴리스에서 받아 bin/ 에 두고,
AI 클라이언트별로 붙여 넣을 명령을 보여 줍니다. Python 은 이 스크립트를 돌리는 데만 쓰이고
실행 파일 자체는 Python 없이 동작합니다.

    python3 install.py                 # 실행 파일 내려받기 + 연결 명령 안내
    python3 install.py --register claude-code   # Claude Code 에 바로 등록
    python3 install.py --register codex         # Codex CLI 에 바로 등록
    python3 install.py --register gemini        # Gemini CLI 에 바로 등록
"""
import getpass, hashlib, os, platform, shutil, stat, subprocess, sys, urllib.request

REPO = "meritz-securities/open-api-mcp"
ASSET_PREFIX = "meritz-mcp"            # 릴리스 자산 이름 앞부분
SERVER_NAME = "meritz"            # 클라이언트에 등록되는 서버 이름
NEEDS_KEY = True             # 앱키가 필요한 서버인지
MCPB = "meritz-open-api"                     # Claude Desktop 설치 파일 이름 앞부분

def target():
    s, m = platform.system(), platform.machine().lower()
    if s == "Darwin":  return ("darwin-arm64" if m in ("arm64", "aarch64") else "darwin-x64"), ""
    if s == "Windows": return "win32-x64", ".exe"
    sys.exit("지원하지 않는 OS 입니다: %s. 소스 실행은 README 의 uv 방식을 보십시오." % s)

def download(url, dst):
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(dst, "wb") as f:
            shutil.copyfileobj(r, f)
        return True
    except Exception as e:
        # 비공개 저장소이거나 네트워크 제한이면 gh CLI 로 시도한다
        if shutil.which("gh"):
            name = url.rsplit("/", 1)[-1]
            r = subprocess.run(["gh", "release", "download", "--repo", REPO, "--pattern", name, "--output", dst, "--clobber"])
            return r.returncode == 0
        print("내려받기 실패: %s" % e)
        return False

def expected_sha256(here, plat, name):
    """릴리스의 SHA256SUMS.txt 에서 이 자산의 체크섬을 찾는다. 못 받으면 None."""
    sums = os.path.join(here, "bin", "SHA256SUMS.txt")
    url = "https://github.com/%s/releases/latest/download/SHA256SUMS.txt" % REPO
    if not download(url, sums):
        return None
    with open(sums, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == name:
                return parts[0].lower()
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    plat, ext = target()
    name = "%s-%s%s" % (ASSET_PREFIX, plat, ext)
    url = "https://github.com/%s/releases/latest/download/%s" % (REPO, name)
    os.makedirs(os.path.join(here, "bin"), exist_ok=True)
    dst = os.path.join(here, "bin", name)
    print("실행 파일 내려받는 중: %s" % url)
    if not download(url, dst):
        sys.exit("실행 파일을 받지 못했습니다. 브라우저에서 위 주소를 직접 열어 bin/ 폴더에 저장한 뒤 다시 실행하십시오.")

    # 내려받은 파일이 릴리스에 올라온 그대로인지 확인한다. 값이 다르면 실행하지 않는다.
    want = expected_sha256(here, plat, name)
    if want is None:
        print("체크섬 파일을 받지 못했습니다. 릴리스 페이지의 SHA256SUMS.txt 와 직접 대조하십시오.")
    else:
        got = sha256(dst)
        if got != want:
            os.remove(dst)
            sys.exit("체크섬이 다릅니다. 받은 파일을 지웠습니다.\n  기대 %s\n  실제 %s" % (want, got))
        print("체크섬 확인: %s" % want)

    if ext == "":
        os.chmod(dst, os.stat(dst).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        subprocess.run(["xattr", "-d", "com.apple.quarantine", dst], capture_output=True)  # Mac 격리 속성 제거
    print("완료: %s\n" % dst)

    env_cc = " --env MERITZ_APP_KEY=발급받은_앱키 --env MERITZ_APP_SECRET=발급받은_시크릿" if NEEDS_KEY else ""
    env_gm = " -e MERITZ_APP_KEY=발급받은_앱키 -e MERITZ_APP_SECRET=발급받은_시크릿" if NEEDS_KEY else ""
    cmds = {
        "claude-code": "claude mcp add %s%s -- \"%s\"" % (SERVER_NAME, env_cc, dst),
        "codex":       "codex mcp add %s%s -- \"%s\"" % (SERVER_NAME, env_cc, dst),
        "gemini":      "gemini mcp add%s %s \"%s\"" % (env_gm, SERVER_NAME, dst),
    }
    reg = None
    if "--register" in sys.argv:
        i = sys.argv.index("--register"); reg = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    if reg in cmds:
        # 셸을 거치지 않고 인자 목록으로 실행한다. 셸을 거치면 앱키에 든 따옴표·$·백틱이
        # 명령으로 해석되고, 명령 문자열이 셸 기록에도 남는다.
        key = os.environ.get("MERITZ_APP_KEY") or getpass.getpass("앱키(APP KEY): ").strip()
        sec = os.environ.get("MERITZ_APP_SECRET") or getpass.getpass("시크릿(APP SECRET): ").strip()
        if not key or not sec:
            sys.exit("앱키와 시크릿이 있어야 등록할 수 있습니다.")
        env = ["MERITZ_APP_KEY=" + key, "MERITZ_APP_SECRET=" + sec]
        argv = {
            "claude-code": ["claude", "mcp", "add", SERVER_NAME] + sum([["--env", e] for e in env], []) + ["--", dst],
            "codex":       ["codex", "mcp", "add", SERVER_NAME] + sum([["--env", e] for e in env], []) + ["--", dst],
            "gemini":      ["gemini", "mcp", "add"] + sum([["-e", e] for e in env], []) + [SERVER_NAME, dst],
        }[reg]
        print("등록 실행: %s mcp add %s …" % (argv[0], SERVER_NAME))
        if not shutil.which(argv[0]):
            sys.exit("%s 명령을 찾지 못했습니다. 해당 도구를 먼저 설치하십시오." % argv[0])
        sys.exit(subprocess.run(argv).returncode)

    print("AI 클라이언트에 연결하는 방법 — 쓰는 것 하나만 고르십시오.\n")
    print("  Claude Desktop : 설치 파일을 내려받아 더블클릭%s" % ("한 뒤 앱키·시크릿 입력" if NEEDS_KEY else ""))
    print("                   https://github.com/%s/releases/latest/download/%s-%s.mcpb\n" % (REPO, MCPB, plat))
    print("  Claude Code    : python3 install.py --register claude-code")
    print("  Codex CLI      : python3 install.py --register codex")
    print("  Gemini CLI     : python3 install.py --register gemini")
    print("  Cursor·VS Code : docs/connect.html 을 내려받아 브라우저로 열고 실행 파일 경로와 앱키를 넣으십시오")
    print("\n  --register 는 앱키를 화면에 찍지 않고 받아 그대로 등록합니다.")
    print("  아래 명령을 직접 붙여 넣으셔도 되지만, 앱키가 셸 기록 파일에 남습니다.\n")
    print("    %s" % cmds["claude-code"])
    print("    %s" % cmds["codex"])
    print("    %s" % cmds["gemini"])
    if NEEDS_KEY:
        print("\n  앱키·시크릿은 개발자 포털(https://openapi.imeritz.com)에서 발급받습니다. 이 스크립트는 저장하지 않습니다.")
    print("\n연결 후 AI 클라이언트를 완전히 종료했다가 다시 실행하십시오.")

if __name__ == "__main__":
    main()
