"""Shared API session -> draft -> engine -> improvement -> persisted MCP replay.

Only the model wire is replaced. Prices are explicit test data, not Meritz quotes.
Expected wealth is derived from next-bar buys at 20 and final valuation at 40.
"""
import asyncio
import copy
import json
import os
import socket

import httpx
import pytest
from fastmcp import Client, FastMCP

from runtime.ai_http_provider import HttpProviderConfig, generate_candidates
from runtime.ai_session import AISession
from runtime.mcp import register_platform
from runtime.service import StrategyRuntime
from runtime.storage import digest


@pytest.mark.skipif(os.name != 'posix', reason='Improvement coordinator locks are POSIX-only')
def test_same_api_session_creates_then_improves_and_replays_from_mcp(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('External network forbidden'))
    runtime = StrategyRuntime(tmp_path, allow_ai_connection=True)
    studio = runtime._ai_studio
    original = {
        'schemaVersion': 1, 'kind': 'dsl', 'name': 'Generated baseline',
        'entry': {'op': 'gt', 'left': {'type': 'field', 'field': 'close'},
                  'right': {'type': 'constant', 'value': 0}},
        'exit': {'op': 'lt', 'left': {'type': 'field', 'field': 'close'},
                 'right': {'type': 'constant', 'value': 0}},
        'quantity': 1, 'allowPyramiding': False,
    }
    increased = {**copy.deepcopy(original), 'name': 'Two shares', 'quantity': 2}
    no_entry = copy.deepcopy(original)
    no_entry.update(name='No trades')
    no_entry['entry']['right']['value'] = 999
    calls = []
    key = 'GENERATED_JOURNEY_KEY_NOT_A_CREDENTIAL'

    class Body(httpx.AsyncByteStream):
        def __init__(self, candidates):
            self.value = json.dumps({'type': 'message', 'role': 'assistant',
                'stop_reason': 'end_turn', 'content': [{'type': 'text',
                'text': json.dumps({'candidates': candidates})}]}).encode()

        async def __aiter__(self):
            yield self.value

    async def wire(request):
        assert request.headers['x-api-key'] == key
        assert str(request.url) == 'https://api.anthropic.com/v1/messages'
        payload = json.loads(request.content)
        assert payload['model'] == 'journey-fixture-model'
        assert 'tools' not in payload
        assert key not in request.content.decode()
        assert 'PRIVATE_PRICE_LABEL' not in request.content.decode()
        calls.append(payload)
        assert len(calls) <= 2, 'Retry must never trigger an additional generation'
        candidates = [original] if len(calls) == 1 else [increased, no_entry]
        return httpx.Response(200, headers={'content-type': 'application/json'}, stream=Body(candidates))

    def send(config, prompt, event):
        return generate_candidates(config, prompt, event,
                                   _transport_factory=lambda: httpx.MockTransport(wire))

    studio.session = AISession(config_factory=HttpProviderConfig, send=send,
                              models=['journey-fixture-model'], management_allowed=True)
    connection = studio.connect_ai_session(apiKey=key, model='journey-fixture-model',
                                          maxOutputTokens=1024, timeoutSeconds=5)
    assert connection['configured'] and connection['authenticated'] is None
    connection_id = connection['configuration']['connectionId']
    preview = studio.create_ai_draft(request='Create one-share test strategy', connectionId=connection_id)
    assert calls == []
    draft = studio.generate_ai_draft(requestId=preview['id'], promptHash=preview['promptHash'],
                                    consentVersion=preview['consentVersion'])
    assert draft['state'] == 'ready' and len(calls) == 1
    assert runtime.store.list('revision') == [] and runtime.store.list('run') == []
    assert draft['modelInvocationVerified'] is False

    # Applying a model result does not implicitly save or execute it.
    revision = runtime.save_strategy(draft['result']['candidates'][0])
    prices = [10, 20, 30, 40]
    data = runtime.import_data([
        {'date': f'2025-01-0{i+1}', 'open': p, 'high': p, 'low': p, 'close': p}
        for i, p in enumerate(prices)], {'kind': 'imported', 'label': 'PRIVATE_PRICE_LABEL'})
    settings = {'initialCash': 100, 'feeBps': 0, 'slippageBps': 0, 'taxBps': 0}
    baseline = runtime.run(revision['id'], data['id'], settings)
    baseline_hash = digest(baseline)
    assert baseline['report']['metrics']['finalEquity'] == 120  # 100 - 20 + 40
    assert baseline['report']['trades'][0]['date'] == '2025-01-02'
    assert runtime.diagnose(baseline['id'])['diagnostics']

    request_id = runtime.create_ai_request(baseline['id'], 'Compare two shares with no trades')['request']['id']
    improvement = studio.preview_ai_http_improvement(requestId=request_id, connectionId=connection_id)
    assert improvement['connectionId'] == connection_id and len(calls) == 1
    assert 'maxBudgetUsd' not in improvement
    consent = {'requestId': request_id, 'promptHash': improvement['promptHash'],
               'consentVersion': improvement['consentVersion']}
    result = studio.generate_ai_http_improvement(**consent)
    assert result['status'] == 'succeeded' and len(calls) == 2
    assert result['submission']['origin'] == 'local-http-generated'
    assert result['submission']['modelInvocationVerified'] is False
    assert result['generation']['connectionId'] == connection_id
    children = [runtime.get_run(item['runId']) for item in result['attempts']]
    assert [r['report']['metrics']['finalEquity'] for r in children] == [140, 100]
    assert children[1]['report']['trades'] == []  # Worse/no-trade result is retained.
    for child in children:
        assert child['parentRunId'] == baseline['id']
        assert child['datasetId'] == baseline['datasetId']
        assert child['manifest']['dataHash'] == baseline['manifest']['dataHash']
        assert child['settings'] == baseline['settings']
    comparison = runtime.compare([baseline['id']] + [r['id'] for r in children])
    assert comparison['sameDataset'] and comparison['sameCosts']
    assert comparison['runs'][1]['deltaReturnPct'] > 0
    assert comparison['runs'][2]['deltaReturnPct'] < 0
    assert digest(runtime.get_run(baseline['id'])) == baseline_hash

    studio.disconnect_ai_session()
    restored = StrategyRuntime(tmp_path)
    assert restored.get_ai_request(request_id) == result
    assert restored._ai_studio.generate_ai_http_improvement(**consent) == result
    assert len(calls) == 2

    async def replay():
        mcp = FastMCP('saved customer journey')
        register_platform(mcp, None, None, restored)
        async with Client(mcp) as client:
            state = (await client.call_tool('meritz_ai_request_get', {'requestId': request_id})).structured_content
            assert state == result
            for child in children:
                saved = (await client.call_tool('meritz_strategy_list', {'revisionId': child['revisionId']})).structured_content
                assert saved['id'] == child['revisionId']
                replayed = (await client.call_tool('meritz_platform_backtest', {
                    'revisionId': child['revisionId'], 'datasetId': child['datasetId'],
                    'settings': child['settings']})).structured_content
                assert replayed['manifest']['resultHash'] == child['manifest']['resultHash']

    asyncio.run(replay())
    assert len(calls) == 2
    assert digest(restored.get_run(baseline['id'])) == baseline_hash
    for file in tmp_path.rglob('*'):
        if file.is_file():
            assert key.encode() not in file.read_bytes(), file
