"""Assemble a local Claude .mcpb candidate from the existing MCP manifest and platform runtime."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


REQUIRED_TOOLS = {
    'meritz_account', 'meritz_market', 'meritz_trading', 'meritz_forex',
    'meritz_strategy_templates', 'meritz_platform_data', 'meritz_platform_backtest',
    'meritz_platform_runs', 'meritz_info',
}
COPY_FROM_MCP = ('README.md', 'DISCLAIMER.md', 'SECURITY.md')
COPY_FROM_PLATFORM = ('LICENSE', 'DISCLAIMER.md', 'THIRD-PARTY-NOTICES.txt')


async def discover_tools(executable: Path) -> list[dict]:
    env = {'PATH': '/usr/bin:/bin', 'MERITZ_NO_ENV_FILE': '1', 'MERITZ_READ_ONLY': '1'}
    async with Client(StdioTransport(command=str(executable), args=['--mcp'], env=env), timeout=60) as client:
        tools = await client.list_tools()
    return [{'name': tool.name, 'description': (tool.description or tool.name)[:4000]} for tool in tools]


def append_backtest_readme(path: Path, tool_names: set[str]):
    strategy = sorted(name for name in tool_names if name.startswith(('meritz_strategy_', 'meritz_platform_', 'meritz_ai_', 'meritz_research_')))
    path.write_text(path.read_text(encoding='utf-8') + '\n\n## 전략 백테스트 추가 기능 (베타)\n\n'
        '이 설치 파일에는 기존 메리츠 Open API 도구와 고객 PC에서 실행하는 전략 백테스트 도구가 함께 들어 있습니다. '
        '백테스트는 과거 시세를 이용한 가상 계산이며 실제 주문을 보내지 않습니다. 앱 설치 후 Claude에서 '
        '`meritz_strategy_templates`로 예제를 확인하고 `meritz_platform_data(source="demo")`로 합성 시세를 준비한 뒤 '
        '`meritz_platform_backtest`로 실행할 수 있습니다. 메리츠 과거 시세를 선택하면 설치 과정에서 입력한 고객 키로 조회합니다.\n\n'
        '추가 도구: ' + ', '.join(strategy) + '\n\n'
        '이 파일은 macOS Apple Silicon용 베타 번들이며, 베타 기간에는 릴리스 자산으로 배포하지 않습니다.\n', encoding='utf-8')


def build(mcp_source: Path, platform_bundle: Path, output: Path) -> Path:
    mcp_source = mcp_source.resolve()
    platform_bundle = platform_bundle.resolve()
    output = output.resolve()
    executable = platform_bundle / 'meritz-strategy-platform'
    source_manifest = mcp_source / 'manifest.json'
    if not source_manifest.is_file() or not executable.is_file():
        raise ValueError('기존 MCP manifest 또는 Strategy Runtime 실행 파일이 없습니다.')
    if not (platform_bundle / '_internal').is_dir():
        raise ValueError('macOS 실행에 필요한 _internal 라이브러리 폴더가 없습니다.')
    if output.exists():
        raise ValueError(f'기존 파일은 덮어쓰지 않습니다: {output}')
    original = json.loads(source_manifest.read_text(encoding='utf-8'))
    source_tools = original.get('tools', [])
    names = {tool.get('name') for tool in source_tools if isinstance(tool, dict)}
    if not {'meritz_account', 'meritz_trading', 'meritz_info'} <= names:
        raise ValueError('기존 MCP의 필수 도구 선언을 찾지 못했습니다.')
    discovered = asyncio.run(discover_tools(executable))
    discovered_names = {tool['name'] for tool in discovered}
    missing = REQUIRED_TOOLS - discovered_names
    if missing:
        raise ValueError('실행 파일에 필수 MCP 도구가 없습니다: ' + ', '.join(sorted(missing)))

    # Preserve curated descriptions for existing tools; use the live packaged server
    # descriptions for newly-added platform tools.
    existing = {tool['name']: tool for tool in source_tools if isinstance(tool, dict) and tool.get('name')}
    manifest = dict(original)
    manifest['version'] = '0.5.0-beta.1'
    manifest['display_name'] = '메리츠 Open API + 전략 백테스트 (베타)'
    manifest['description'] = '기존 메리츠 Open API 도구와 로컬 전략 백테스트를 한 번에 설치합니다.'
    manifest['long_description'] = (original.get('long_description', '') +
        ' 이 베타 번들은 기존 메리츠 Open API 도구에 고객 PC용 전략 검증·가상 백테스트를 함께 제공합니다. '
        '백테스트 실행은 실제 주문 API에 연결되지 않습니다. 베타 기간에는 릴리스 자산으로 배포하지 않습니다.')
    manifest['tools'] = [existing.get(tool['name'], tool) for tool in discovered]
    previous_env = original['server']['mcp_config']['env']
    manifest['server'] = {
        'type': 'binary',
        'entry_point': 'bin/meritz-strategy-platform',
        'mcp_config': {
            'command': '${__dirname}/bin/meritz-strategy-platform',
            'args': ['--mcp'],
            'env': previous_env,
        },
    }
    manifest['compatibility'] = {'platforms': ['darwin']}

    output.parent.mkdir(parents=True, exist_ok=True)
    npx = 'npx.cmd' if os.name == 'nt' else 'npx'
    with tempfile.TemporaryDirectory(prefix='meritz-strategy-mcpb-') as temp:
        stage = Path(temp) / 'stage'
        (stage / 'bin').mkdir(parents=True)
        shutil.copy2(executable, stage / 'bin/meritz-strategy-platform')
        (stage / 'bin/meritz-strategy-platform').chmod(0o755)
        shutil.copytree(platform_bundle / '_internal', stage / 'bin/_internal')
        for name in COPY_FROM_MCP:
            source = mcp_source / name
            if source.is_file():
                shutil.copy2(source, stage / name)
        for name in COPY_FROM_PLATFORM:
            source = platform_bundle / name
            if not source.is_file():
                raise ValueError(f'번들에 필수 고지 파일이 없습니다: {name}')
            shutil.copy2(source, stage / name)
        append_backtest_readme(stage / 'README.md', discovered_names)
        (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        subprocess.run([npx, '--yes', '@anthropic-ai/mcpb', 'validate', str(stage / 'manifest.json')], check=True)
        subprocess.run([npx, '--yes', '@anthropic-ai/mcpb', 'pack', str(stage), str(output)], check=True)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mcp-source', type=Path, required=True, help='Existing open-api-mcp checkout')
    parser.add_argument('--platform-bundle', type=Path, required=True, help='Built Strategy Runtime onedir')
    parser.add_argument('--output', type=Path, required=True, help='New .mcpb output path; existing file is never overwritten')
    args = parser.parse_args()
    try:
        result = build(args.mcp_source, args.platform_bundle, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'Claude MCPB 생성 실패: {error}') from None
    print(f'기존 MCP 통합 검수 파일 생성: {result} ({result.stat().st_size // 1024 // 1024} MB)')


if __name__ == '__main__':
    main()
