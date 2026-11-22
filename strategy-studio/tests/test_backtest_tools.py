"""Protocol-level tests use mocked prices, never customer credentials or network."""
import asyncio
from unittest.mock import patch

from fastmcp import Client, FastMCP
from meritz.backtest_tools import register_backtest, UI_URI, TOOL_NAMES
from meritz.backtest_engine import run_backtest, demo_candles


def test_protocol_ui_demo_compare_trade_and_errors():
    async def check():
        server = FastMCP('test')
        register_backtest(server, None, None)
        async with Client(server) as client:
            listed = await client.list_tools()
            assert {t.name for t in listed} == set(TOOL_NAMES)
            code_tool = next(t for t in listed if t.name == TOOL_NAMES[5])
            assert code_tool.annotations.readOnlyHint is False
            demo_tool = next(t for t in listed if t.name == TOOL_NAMES[0])
            assert demo_tool.meta['ui']['resourceUri'] == UI_URI
            resource = await client.read_resource(UI_URI)
            assert resource[0].mimeType == 'text/html;profile=mcp-app'
            assert resource[0].meta['ui']['csp'] == {'connectDomains': [], 'resourceDomains': []}
            assert 'Meritz' in resource[0].text
            result = await client.call_tool(TOOL_NAMES[0], {})
            report = result.structured_content['report']
            assert report['source']['kind'] == 'synthetic'
            compared = await client.call_tool(TOOL_NAMES[2], {'runId': report['runId'], 'fast': 8, 'slow': 30})
            data = compared.structured_content
            assert data['baseline']['runId'] == report['runId']
            assert data['candidate']['runId'] != report['runId']
            trade = await client.call_tool(TOOL_NAMES[3], {'runId': report['runId'], 'tradeId': report['trades'][0]['id']})
            assert trade.structured_content['trade'] == report['trades'][0]
            bad = await client.call_tool(TOOL_NAMES[0], {'fast': 20, 'slow': 5}, raise_on_error=False)
            assert bad.is_error
            bad = await client.call_tool(TOOL_NAMES[2], {'runId': '../secret', 'fast': 8, 'slow': 30}, raise_on_error=False)
            assert bad.is_error
    asyncio.run(check())


def test_actual_data_path_and_warning_isolation():
    async def check():
        server = FastMCP('test')
        register_backtest(server, None, None)
        fixture = {'candles': demo_candles(), 'source': {'kind': 'meritz', 'label': '테스트용 모의 응답',
            'symbol': '005930', 'market': 'TJ', 'startDate': '2025-01-02', 'endDate': '2025-12-31',
            'fetchedAt': '2026-01-01T00:00:00Z'}, 'warnings': ['DATA_WARNING']}
        def calculate(candles, settings, source):
            report = run_backtest(candles, settings, source)
            if settings['fast'] == 5:
                report['warnings'].append('BASELINE_ONLY')
            return report
        with patch('meritz.backtest_tools.load_market_data', return_value=fixture) as loader, patch('meritz.backtest_tools.run_backtest', side_effect=calculate):
            async with Client(server) as client:
                result = await client.call_tool(TOOL_NAMES[1], {'symbol':'005930','startDate':'2025-01-02','endDate':'2025-12-31','account':'사용자계좌'})
                report = result.structured_content['report']
                assert report['source']['kind'] == 'meritz'
                loader.assert_called_once_with(None,None,'005930','2025-01-02','2025-12-31','사용자계좌')
                assert 'BASELINE_ONLY' in report['warnings']
                result = await client.call_tool(TOOL_NAMES[2], {'runId':report['runId'],'fast':8,'slow':30})
                candidate = result.structured_content['candidate']
                assert 'DATA_WARNING' in candidate['warnings']
                assert 'BASELINE_ONLY' not in candidate['warnings']
                assert loader.call_count == 1
    asyncio.run(check())


def test_provider_errors_do_not_leak_secrets_or_fallback():
    async def check():
        server=FastMCP('test'); register_backtest(server,None,None)
        with patch('meritz.backtest_tools.load_market_data', side_effect=RuntimeError('SECRET_TOKEN_DO_NOT_ECHO')):
            async with Client(server) as client:
                result=await client.call_tool(TOOL_NAMES[1],{'symbol':'005930','startDate':'2025-01-01','endDate':'2025-06-01'},raise_on_error=False)
                assert result.is_error
                assert 'SECRET_TOKEN_DO_NOT_ECHO' not in str(result)
                assert '합성 시세로 대체하지 않았습니다' in str(result)
    asyncio.run(check())


def test_http_keeps_existing_tool_surface():
    from meritz.server import build_server
    async def check():
        with patch.dict('os.environ', {'MCP_TYPE':'streamable-http', 'MERITZ_NO_ENV_FILE':'1'}):
            server,_=build_server()
            async with Client(server) as client:
                assert not ({t.name for t in await client.list_tools()} & set(TOOL_NAMES))
    asyncio.run(check())


def test_platform_stdio_exposes_only_shared_runtime_backtest_tools(tmp_path):
    from meritz.server import build_server
    async def check():
        with patch.dict('os.environ', {'MCP_TYPE':'stdio', 'MERITZ_NO_ENV_FILE':'1',
                                      'MERITZ_STRATEGY_HOME':str(tmp_path / 'strategy-home')}):
            server, _ = build_server(enable_platform=True)
            async with Client(server) as client:
                names = {tool.name for tool in await client.list_tools()}
                assert set(TOOL_NAMES).isdisjoint(names)
                assert {'meritz_platform_backtest', 'meritz_platform_data'} <= names
    asyncio.run(check())


def test_strategy_lab_mcp_entry_enables_platform_runtime(monkeypatch):
    import runpy
    import sys
    from pathlib import Path
    from meritz import server as server_module
    entry = Path(__file__).resolve().parents[1] / 'strategy_app_entry.py'
    with patch.object(server_module, 'main') as mocked:
        monkeypatch.setattr(sys, 'argv', [str(entry), '--mcp'])
        runpy.run_path(str(entry), run_name='__main__')
        mocked.assert_called_once_with(enable_platform=True)


def test_strategy_lab_entry_dispatches_strategy_only_mcp(monkeypatch):
    import runpy
    import sys
    from pathlib import Path
    from runtime import strategy_mcp_server
    entry = Path(__file__).resolve().parents[1] / 'strategy_app_entry.py'
    with patch.object(strategy_mcp_server, 'main') as mocked:
        monkeypatch.setattr(sys, 'argv', [str(entry), '--strategy-mcp'])
        runpy.run_path(str(entry), run_name='__main__')
        mocked.assert_called_once_with()


def test_strategy_lab_entry_default_opens_local_browser(monkeypatch):
    import runpy
    import sys
    from pathlib import Path
    from meritz import backtest_local
    entry = Path(__file__).resolve().parents[1] / 'strategy_app_entry.py'
    with patch.object(backtest_local, 'main') as mocked:
        monkeypatch.setattr(sys, 'argv', [str(entry)])
        runpy.run_path(str(entry), run_name='__main__')
        mocked.assert_called_once_with(open_browser=True)
