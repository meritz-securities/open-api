import asyncio
import sys
from unittest.mock import patch
import pytest
from fastmcp import Client, FastMCP
from meritz.backtest_tools import register_backtest
from meritz.backtest_engine import demo_candles

BUY='def on_bar(ctx):\n return {"action":"buy","quantity":1,"reason":"customer code"} if ctx["index"]==0 else {"action":"hold"}'
HOLD='def on_bar(ctx):\n return {"action":"hold"}'


def test_syntax_error_before_any_data_fetch():
    async def go():
        s=FastMCP('test');register_backtest(s,None,None)
        with patch('meritz.backtest_tools.load_market_data') as fetch:
            async with Client(s) as c:
                r=await c.call_tool('meritz_backtest_code',{'code':'def on_bar(ctx)\n pass','source':'demo'},raise_on_error=False)
                assert r.is_error and '1행' in str(r)
                fetch.assert_not_called()
    asyncio.run(go())


@pytest.mark.skipif(sys.platform!='darwin',reason='Native macOS acceptance')
def test_customer_code_real_mcp_path_with_mock_provider():
    async def go():
        s=FastMCP('test');register_backtest(s,None,None)
        fixture={'candles':demo_candles(),'source':{'kind':'meritz','label':'mock provider','symbol':'005930','market':'TJ'},'warnings':['mock provider warning']}
        with patch('meritz.backtest_tools.load_market_data',return_value=fixture) as fetch:
            async with Client(s) as c:
                market={'account':'my-account','source':'meritz','symbol':'005930',
                        'startDate':'2025-01-02','endDate':'2025-06-30'}
                a=await c.call_tool('meritz_backtest_code',{'code':BUY,**market})
                b=await c.call_tool('meritz_backtest_code',{'code':HOLD,**market})
                ra,rb=a.structured_content['report'],b.structured_content['report']
                assert ra['strategy']['interface']=='on_bar_v1'
                assert ra['metrics']['tradeCount']==1 and rb['metrics']['tradeCount']==0
                assert ra['strategy']['codeHash']!=rb['strategy']['codeHash']
                assert ra['runtime']['accounting']=='host-computed'
                assert all(ra['runtime']['checks'].values())
                assert ra['source']['kind']=='meritz' and 'my-account' not in str(ra)
                assert fetch.call_count==2
                trade=await c.call_tool('meritz_backtest_trade',{'runId':ra['runId'],'tradeId':ra['trades'][0]['id']})
                assert trade.structured_content['trade']['quantity']==1
                refused=await c.call_tool('meritz_backtest_compare',{'runId':ra['runId']},raise_on_error=False)
                assert refused.is_error
    asyncio.run(go())


@pytest.mark.parametrize('arguments', [
    {'code': HOLD},
    {'code': HOLD, 'source':'meritz'},
    {'code': HOLD, 'source':'meritz','symbol':'005930'},
    {'code': HOLD, 'source':'meritz','symbol':'005930','startDate':'2025-01-02'},
    {'code': HOLD, 'source':'meritz','startDate':'2025-01-02','endDate':'2025-06-30'},
    {'code': HOLD, 'source':'demo','symbol':'005930'},
    {'code': HOLD, 'source':'demo','startDate':'2025-01-02'},
    {'code': HOLD, 'source':'demo','endDate':'2025-06-30'},
    {'code': HOLD, 'source':'demo','account':'selected'},
])
def test_source_and_market_fields_must_be_explicit_and_consistent(arguments):
    async def go():
        server=FastMCP('test');register_backtest(server,None,None)
        with patch('meritz.backtest_tools.load_market_data') as fetch, patch('meritz.backtest_tools.sandbox_self_test') as probe:
            async with Client(server) as client:
                result=await client.call_tool('meritz_backtest_code',arguments,raise_on_error=False)
                assert result.is_error
                fetch.assert_not_called()
                probe.assert_not_called()
    asyncio.run(go())


def test_preflight_probe_is_reused_by_session():
    probe_result={'platform':'macOS','mechanism':'sandbox-exec','checks':{'fixture':True}}
    captured=[]

    class Session:
        def __init__(self,code,*,verified_sandbox):
            captured.append((code,verified_sandbox))
            self.sandbox=verified_sandbox
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def decide(self,observation):return {'action':'hold'}

    async def go():
        server=FastMCP('test');register_backtest(server,None,None)
        with patch('meritz.backtest_tools.ensure_supported'), \
             patch('meritz.backtest_tools.sandbox_self_test',return_value=probe_result) as probe, \
             patch('meritz.backtest_tools.StrategySession',Session), \
             patch('meritz.backtest_tools.load_market_data') as fetch:
            async with Client(server) as client:
                result=await client.call_tool('meritz_backtest_code',{'code':HOLD,'source':'demo'})
                assert result.structured_content['report']['metrics']['tradeCount']==0
                probe.assert_called_once_with()
                fetch.assert_not_called()
        assert captured==[(HOLD,probe_result)]
    asyncio.run(go())
