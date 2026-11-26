import asyncio

from runtime.strategy_mcp_server import build_server


def test_strategy_server_exposes_no_account_or_order_tools(monkeypatch, tmp_path):
    monkeypatch.setenv('MERITZ_NO_ENV_FILE', '1')
    monkeypatch.setenv('MERITZ_STRATEGY_HOME', str(tmp_path))
    monkeypatch.setenv('MERITZ_READ_ONLY', '1')
    server = build_server()
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert 'meritz_platform_backtest' in names
    assert 'meritz_platform_data' in names
    assert 'meritz_strategy_templates' in names
    assert not ({'meritz_trading', 'meritz_forex', 'meritz_account'} & names)
    assert not any(name.startswith('meritz_backtest_') for name in names)
