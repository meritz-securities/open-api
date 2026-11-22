"""Register a packaged Studio with Claude Code using its supported CLI."""
from pathlib import Path


def write_launcher(bundle: Path, system: str, mac_runtime='meritz-strategy-platform'):
    if system == 'Windows':
        (bundle / 'Connect-to-Claude-Code.cmd').write_text(
            '@echo off\n'
            'call claude mcp add --scope user --transport stdio meritz-studio -- "%~dp0meritz-strategy-platform.exe" --strategy-mcp\n'
            'if errorlevel 1 (pause & exit /b 1)\n'
            'call claude mcp get meritz-studio\npause\n',
            encoding='ascii', newline='\r\n')
    else:
        path = bundle / 'Connect-to-Claude-Code.command'
        path.write_text(
            '#!/bin/zsh\n'
            'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"\n'
            f'claude mcp add --scope user --transport stdio meritz-studio -- "${{0:A:h}}/{mac_runtime}" --strategy-mcp && '
            'claude mcp get meritz-studio\n'
            'read -k 1 "?Press any key to close."\n', encoding='utf-8')
        path.chmod(0o755)
