#!/usr/bin/env python3
"""플랫폼별 Claude Desktop 설치 파일(.mcpb)을 만듭니다.

    python3 make_bundle.py darwin-arm64     # bin/meritz-mcp 가 있어야 한다
    python3 make_bundle.py darwin-x64
    python3 make_bundle.py win32-x64        # bin/meritz-mcp.exe

manifest.json 은 개발자용(uvx 실행) 정의입니다. 여기서는 그 정의를 읽어
server.type 을 binary 로 바꾼 사본을 dist/stage-<플랫폼>/ 에 만들고,
실행 파일·README·고지문을 넣어 dist/<이름>-<플랫폼>.mcpb 로 묶습니다.

왜 플랫폼마다 따로 묶나 — 한 파일에 세 실행 파일을 다 넣으면 100MB 를 넘고,
고객은 자기 OS 것 하나만 필요합니다.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 러너의 콘솔은 cp1252 라 한글 출력에서 UnicodeEncodeError 가 난다. 출력만 UTF-8 로 고정한다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
PLATFORMS = {
    "darwin-arm64": ("darwin", "meritz-mcp"),
    "darwin-x64":   ("darwin", "meritz-mcp"),
    "win32-x64":    ("win32",  "meritz-mcp.exe"),
}
COPY = ["README.md", "LICENSE", "DISCLAIMER.md", "SECURITY.md", "THIRD-PARTY-NOTICES.txt"]


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in PLATFORMS:
        print(__doc__); return 2
    plat = sys.argv[1]
    os_name, exe = PLATFORMS[plat]
    src_bin = ROOT / "bin" / exe
    if not src_bin.exists():
        print(f"{src_bin} 가 없습니다. ./build_binary.sh 를 먼저 실행하세요."); return 1

    m = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    name = m["name"]
    m["server"] = {
        "type": "binary",
        "entry_point": f"bin/{exe}",
        "mcp_config": {
            "command": f"${{__dirname}}/bin/{exe}",
            "args": [],
            "env": m["server"]["mcp_config"]["env"],
        },
    }
    # 실행 파일이라 런타임 요구가 없다. 대신 플랫폼을 못 박는다.
    m["compatibility"] = {"platforms": [os_name]}
    m["long_description"] = (m.get("long_description", "") +
        " 이 설치 파일은 Python·uv 없이 동작하는 단일 실행 파일을 담고 있습니다"
        f" ({plat}).")

    stage = ROOT / "dist" / f"stage-{plat}"
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "bin").mkdir(parents=True)
    shutil.copy2(src_bin, stage / "bin" / exe)
    os.chmod(stage / "bin" / exe, 0o755)
    for f in COPY:
        if (ROOT / f).exists():
            shutil.copy2(ROOT / f, stage / f)
    (stage / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    out = ROOT / "dist" / f"{name}-{plat}.mcpb"
    if out.exists():
        out.unlink()
    npx = "npx.cmd" if os.name == "nt" else "npx"   # Windows 는 .cmd 를 직접 불러야 찾는다
    subprocess.run([npx, "--yes", "@anthropic-ai/mcpb", "validate", str(stage / "manifest.json")], check=True)
    subprocess.run([npx, "--yes", "@anthropic-ai/mcpb", "pack", str(stage), str(out)], check=True)
    print(f"\n만들어진 파일: {out}  ({out.stat().st_size // 1024 // 1024} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
