import asyncio

import requests
from fastmcp import Client


def test_combined_mcp_keeps_orders_opt_in_and_strategy_mcp_has_no_order_tool(tmp_path, monkeypatch):
    monkeypatch.setenv('MERITZ_NO_ENV_FILE', '1')
    monkeypatch.setenv('MERITZ_APP_KEY', 'test-app-key')
    monkeypatch.setenv('MERITZ_APP_SECRET', 'test-app-secret')
    monkeypatch.setenv('MERITZ_READ_ONLY', '1')
    monkeypatch.setenv('MERITZ_STRATEGY_HOME', str(tmp_path))
    monkeypatch.delenv('MCP_TYPE', raising=False)

    def no_http(*args, **kwargs):
        raise AssertionError('A read-only order attempt must not send an HTTP request')

    monkeypatch.setattr(requests.Session, 'request', no_http)

    from meritz.server import build_server
    from runtime.strategy_mcp_server import build_server as build_strategy_only

    async def check():
        combined, settings = build_server(enable_platform=True)
        tools = {tool.name for tool in await combined.list_tools()}
        assert settings.read_only is True
        assert {'meritz_trading', 'meritz_platform_backtest'} <= tools

        async with Client(combined) as client:
            response = await client.call_tool('meritz_trading', {
                'api_type': 'orders_buy',
                'account': settings.label,
                'params': {'iscd': 'A005930', 'oder_unpr': '0', 'odqt': '1',
                           'oder_cls_code': '05', 'oder_cond_cls_code': '0', 'exch_kind_code': '01'},
            }, raise_on_error=False)
            assert response.is_error
            assert '조회 전용' in str(response)

        strategy_tools = {tool.name for tool in await build_strategy_only().list_tools()}
        assert 'meritz_platform_backtest' in strategy_tools
        assert not ({'meritz_trading', 'meritz_account', 'meritz_forex'} & strategy_tools)

    asyncio.run(check())
