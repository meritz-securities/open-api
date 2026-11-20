"""Actual MCP transport -> shared engine -> durable Runtime retrieval.

Synthetic quotes only; candidates are explicitly supplied, no model connection.
Requires the research Runtime implementation in the integration checkout.
"""
import asyncio

from fastmcp import Client, FastMCP
from runtime.mcp import register_platform
from runtime.service import StrategyRuntime


def test_mcp_research_uses_shared_engine_and_restores_identical_results(tmp_path):
    async def check():
        service = StrategyRuntime(tmp_path)
        mcp = FastMCP('research-engine-integration')
        register_platform(mcp, None, None, service)
        definition = {
            'schemaVersion': 1, 'kind': 'dsl', 'name': 'MCP fixed quantity', 'quantity': 1,
            'entry': {'op': 'gt', 'left': {'type': 'field', 'field': 'close'},
                      'right': {'type': 'constant', 'value': 0}},
            'exit': {'op': 'lt', 'left': {'type': 'field', 'field': 'close'},
                     'right': {'type': 'constant', 'value': 0}},
        }
        prices = [10, 20, 30, 30, 20, 10]
        bars = [{'date': f'2025-01-{index+1:02d}', 'open': price, 'high': price,
                 'low': price, 'close': price} for index, price in enumerate(prices)]
        async with Client(mcp) as client:
            async def call(name, **args):
                return (await client.call_tool(name, args)).data
            strategy = await call('meritz_strategy_save', definition=definition)
            data = await call('meritz_platform_data', source='imported', bars=bars,
                              metadata={'kind': 'imported', 'label': 'synthetic MCP oracle'})
            created = await call('meritz_research_create', source={
                'revisionId': strategy['id'], 'datasetId': data['id'],
                'settings': {'initialCash': 100, 'feeBps': 0, 'taxBps': 0, 'slippageBps': 0}},
                purpose='Compare position sizes', trainFraction=.5)
            ident = created['session']['id']
            assert service.list_runs()['runs'] == []
            ready = await call('meritz_research_prepare', researchId=ident)
            await call('meritz_ai_candidates_submit', requestId=ready['training']['aiRequestId'],
                       candidates=[{**definition, 'name': 'MCP candidate', 'quantity': 3}])
            frozen = await call('meritz_research_freeze', researchId=ident)
            result = await call('meritz_research_evaluate', researchId=ident)
            # Hand ledger: buy at20, sell nothing, close10 => cash80+10 / cash40+30.
            assert [slot['metrics']['finalEquity'] for slot in result['evaluation']['slots']] == [90, 70]
            assert result['frozen'] == frozen['frozen']
            assert result['evaluation']['status'] == 'succeeded'
            assert await call('meritz_research_get', researchId=ident) == service.get_research(ident)
            before = {run['id'] for run in service.list_runs()['runs']}
            assert len(before) == 4  # training baseline+candidate; heldout baseline+candidate
            assert await call('meritz_research_evaluate', researchId=ident) == result
            assert {run['id'] for run in service.list_runs()['runs']} == before
            cancelled = await call('meritz_research_cancel', researchId=ident, phase='evaluation')
            assert cancelled['cancellation']['effective'] is False
            assert cancelled['evaluation'] == result['evaluation']
        reopened = StrategyRuntime(tmp_path)
        assert reopened.get_research(ident) == result
        assert {run['id'] for run in reopened.list_runs()['runs']} == before
    asyncio.run(check())
