"""Stored candidate revisions remain addressable beyond the recent-list limit."""
import asyncio

import pytest
from fastmcp import Client, FastMCP

from runtime.mcp import register_platform
from runtime.service import StrategyRuntime
from runtime.storage import encode_body


def test_old_revision_direct_dispatch_and_mcp_preserve_snapshot(tmp_path):
    service = StrategyRuntime(tmp_path)
    original = service.save_strategy(service.templates()['templates'][0])
    child = service.save_strategy({**original['definition'], 'name': '후보'}, original['id'])
    for index in range(101):
        service.save_strategy({**original['definition'], 'name': str(index)})
    assert child['id'] not in {r['id'] for r in service.list_strategies()['strategies']}
    before = service.store.list_summaries('revision', limit=1000)
    assert service.dispatch('get_strategy', {'revisionId': child['id']}) == child

    async def check():
        mcp = FastMCP('revision-read-test')
        register_platform(mcp, None, None, service)
        async with Client(mcp) as client:
            listing = (await client.call_tool('meritz_strategy_list', {})).data
            single = (await client.call_tool('meritz_strategy_list', {'revisionId': child['id']})).data
            assert listing == service.list_strategies()
            assert single == child
            missing = await client.call_tool('meritz_strategy_list', {'revisionId': ''}, raise_on_error=False)
            assert missing.is_error
    asyncio.run(check())
    assert service.get_strategy(original['id']) == original
    assert service.store.list_summaries('revision', limit=1000) == before
    assert service.list_runs() == {'runs': [], 'validationJobs': []}


@pytest.mark.parametrize('identifier', [None, '', [], 'x' * 81, 'missing'])
def test_invalid_or_missing_revision_fails_without_writes(tmp_path, identifier):
    service = StrategyRuntime(tmp_path)
    with pytest.raises(ValueError):
        service.get_strategy(identifier)
    assert service.list_strategies() == {'strategies': []}


def test_tampered_revision_is_not_returned(tmp_path):
    service = StrategyRuntime(tmp_path)
    revision = service.save_strategy(service.templates()['templates'][0])
    revision['definition']['name'] = 'tampered'
    with service.store.connection() as con:
        con.execute('UPDATE objects SET body=? WHERE kind=? AND id=?',
                    (encode_body(revision), 'revision', revision['id']))
    with pytest.raises(ValueError, match='무결성'):
        service.dispatch('get_strategy', {'revisionId': revision['id']})
