"""Safely register the local strategy-only MCP with Claude Desktop on macOS."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from datetime import datetime


SERVER_NAME = "meritz-strategy-platform"


def install(config_path: Path, executable: Path, *, now: datetime | None = None) -> str:
    config_path = Path(config_path).expanduser()
    executable = Path(executable).expanduser().resolve()
    if not executable.is_file():
        raise ValueError("실행 파일을 찾을 수 없습니다. 앱 폴더를 옮기거나 삭제했는지 확인하세요.")
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError("Claude 설정 파일을 읽을 수 없습니다. 파일을 직접 고치지 않고 중단했습니다.") from None
        if not isinstance(config, dict):
            raise ValueError("Claude 설정의 최상위 형식이 JSON 객체가 아니어서 변경하지 않았습니다.")
        mode = config_path.stat().st_mode & 0o777
    else:
        config = {}
        mode = 0o600

    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("Claude 설정의 mcpServers 형식이 객체가 아니어서 변경하지 않았습니다.")
    entry = {"command": str(executable), "args": ["--strategy-mcp"],
             "env": {"MERITZ_READ_ONLY": "1"}}
    existing = servers.get(SERVER_NAME)
    if existing is not None:
        if existing == entry:
            return "이미 Claude에 등록되어 있습니다. Claude Desktop을 완전히 종료한 뒤 다시 열면 됩니다."
        raise ValueError(f"Claude에 '{SERVER_NAME}' 이름의 다른 서버가 이미 있어 덮어쓰지 않았습니다.")

    servers[SERVER_NAME] = entry
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if config_path.exists():
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        backup = config_path.with_name(f"{config_path.name}.bak-{stamp}")
        if backup.exists():
            raise ValueError("같은 시각의 설정 백업 파일이 이미 있어 중단했습니다.")
        shutil.copy2(config_path, backup)

    payload = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temp_path = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{config_path.name}.", suffix=".tmp", dir=config_path.parent)
        temp_path = Path(temp_name)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, config_path)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        if backup is not None:
            backup.unlink(missing_ok=True)
        raise
    return (f"Claude에 전략 백테스트 MCP를 등록했습니다.\n"
            f"설정 백업: {backup if backup else '기존 설정 없음'}\n"
            "Claude Desktop을 완전히 종료한 뒤 다시 열어야 도구가 표시됩니다.\n"
            "이 연결은 전략·시세·백테스트 도구만 제공하며 주문·환전 도구는 포함하지 않습니다.")


def main() -> int:
    if sys.platform != "darwin":
        print("이 원클릭 등록 파일은 macOS Claude Desktop 전용입니다.", file=sys.stderr)
        return 2
    config_path = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    try:
        message = install(config_path, Path(sys.executable))
    except (OSError, ValueError) as error:
        print(f"Claude 연결을 등록하지 못했습니다: {error}", file=sys.stderr)
        return 1
    print(message)
    return 0
