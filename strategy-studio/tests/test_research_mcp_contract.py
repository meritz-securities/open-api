"""MCP routing/annotations only; engine correctness is tested independently."""
import asyncio
from fastmcp import Client, FastMCP
from runtime.mcp import register_platform

class RecordingRuntime:
    def __init__(self):
        self.calls = []

    def dispatch(self, action, arguments):
        self.calls.append((action, arguments))
        return {'action': action, 'arguments': arguments}

def test_research_tools_route_to_shared_runtime_and_keep_read_only_hints():
    async def check():
        runtime = RecordingRuntime()
        mcp = FastMCP('research-routing-test')
        names = register_platform(mcp, None, None, runtime)
        assert len(names) == len(set(names))
        assert {'meritz_strategy_templates', 'meritz_strategy_robustness',
                'meritz_ai_request_create', 'meritz_studio_open'} <= set(names)
        ident = 'a' * 32
        source = {'revisionId': 'b' * 32, 'datasetId': 'c' * 32,
                  'settings': {'initialCash': 100, 'feeBps': 0, 'taxBps': 0, 'slippageBps': 0}}
        cases = [
            ('create', 'create_research', {'source': source, 'purpose': '기간을 분리해 비교', 'trainFraction': .5, 'researchId': ident}),
            ('prepare', 'prepare_research_training', {'researchId': ident}),
            ('get', 'get_research', {'researchId': ident}),
            ('list', 'list_research', {'limit': 7, 'source': {'datasetId': 'c' * 32}}),
            ('freeze', 'freeze_research', {'researchId': ident}),
            ('evaluate', 'evaluate_research', {'researchId': ident}),
            ('cancel', 'cancel_research', {'researchId': ident, 'phase': 'evaluation'}),
        ]
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            for suffix, action, arguments in cases:
                name = 'meritz_research_' + suffix
                assert tools[name].annotations.readOnlyHint == (suffix in ('get', 'list'))
                assert tools[name].annotations.destructiveHint is False
                result = (await client.call_tool(name, arguments)).data
                assert result == {'action': action, 'arguments': arguments}
                assert runtime.calls[-1] == (action, arguments)
            assert len(runtime.calls) == 7
    asyncio.run(check())


def test_research_rejects_invalid_pagination_and_cancel_phase_before_dispatch():
    """An invalid client request must not reach a state-changing Runtime call."""
    import pytest
    from fastmcp.exceptions import ToolError

    async def check():
        runtime = RecordingRuntime()
        mcp = FastMCP('research-input-boundary')
        register_platform(mcp, None, None, runtime)
        async with Client(mcp) as client:
            for limit in (0, 101, True, '10', 1.5):
                with pytest.raises(ToolError):
                    await client.call_tool('meritz_research_list', {'limit': limit})
            for phase in ('all', '', 'run', None):
                with pytest.raises(ToolError):
                    await client.call_tool('meritz_research_cancel', {
                        'researchId': 'a' * 32, 'phase': phase,
                    })
        assert runtime.calls == []
    asyncio.run(check())


def test_research_internal_failure_does_not_disclose_runtime_exception():
    """Unexpected failures must not disclose local paths or provider secrets."""
    import pytest
    from fastmcp.exceptions import ToolError

    class FailingRuntime:
        def dispatch(self, action, arguments):
            raise RuntimeError('/private/customer/account secret-test-token')

    async def check():
        mcp = FastMCP('research-error-boundary')
        register_platform(mcp, None, None, FailingRuntime())
        async with Client(mcp) as client:
            with pytest.raises(ToolError) as caught:
                await client.call_tool('meritz_research_get', {'researchId': 'a' * 32})
            message = str(caught.value)
            assert '로컬 전략 요청을 처리하지 못했습니다' in message
            assert 'secret-test-token' not in message
            assert '/private/customer' not in message
    asyncio.run(check())
