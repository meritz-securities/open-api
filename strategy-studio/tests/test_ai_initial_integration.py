"""Real session + HTTP request construction + draft storage + engine; fake wire only."""
import json
import socket

import httpx
import pytest

from runtime.ai_drafts import DraftController, DraftError
from runtime.ai_http_provider import HttpProviderConfig, generate_candidates
from runtime.ai_session import AISession
from runtime.service import StrategyRuntime
from runtime.storage import Store


def test_initial_ai_chain_stays_local_until_explicit_send_then_explicit_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('external network forbidden'))
    calls = []
    strategy = {'schemaVersion': 1, 'kind': 'dsl', 'name': '단순 조건',
                'entry': {'op': 'gt', 'left': {'type': 'field', 'field': 'close'}, 'right': {'type': 'constant', 'value': 0}},
                'exit': {'op': 'lt', 'left': {'type': 'field', 'field': 'close'}, 'right': {'type': 'constant', 'value': 0}},
                'quantity': 1, 'allowPyramiding': False}
    class WireBytes(httpx.AsyncByteStream):
        def __init__(self, value): self.value = json.dumps(value).encode()
        async def __aiter__(self): yield self.value
    async def wire(request):
        assert str(request.url) == 'https://api.anthropic.com/v1/messages'
        assert request.headers['x-api-key'] == 'FIXTURE_PRIVATE_KEY'
        body = json.loads(request.content)
        assert body['model'] == 'fixture-model'
        assert 'FIXTURE_PRIVATE_KEY' not in request.content.decode()
        assert 'tools' not in body
        calls.append(body)
        return httpx.Response(200, headers={'content-type': 'application/json'}, stream=WireBytes({'type': 'message', 'role': 'assistant', 'stop_reason': 'end_turn',
                             'content': [{'type': 'text', 'text': json.dumps({'candidates': [strategy]})}]}))
    def send(config, prompt, event):
        return generate_candidates(config, prompt, event, _transport_factory=lambda: httpx.MockTransport(wire))
    session = AISession(config_factory=HttpProviderConfig, send=send, models=['fixture-model'], management_allowed=True)
    state = session.connect(apiKey='FIXTURE_PRIVATE_KEY', model='fixture-model', timeoutSeconds=2)
    assert calls == [] and state['authenticated'] is None
    store = Store(tmp_path)
    connection_id = state['configuration']['connectionId']
    with session.lease(connection_id) as lease:
        preview = DraftController(store, lease.generate, lease.metadata).create('조건이 맞으면 한 주 매수하는 전략')
    assert calls == []
    with session.lease(connection_id) as lease:
        controller = DraftController(store, lease.generate, lease.metadata)
        ready = controller.generate(preview['id'], preview['promptHash'], preview['consentVersion'])
        assert ready['state'] == 'ready', ready
        assert controller.generate(preview['id'], preview['promptHash'], preview['consentVersion']) == ready
    assert len(calls) == 1
    assert store.list('run') == [] and store.list('revision') == []
    session.disconnect()
    restored = DraftController(Store(tmp_path)).get(preview['id'])
    assert restored['result'] == ready['result']
    assert restored['modelInvocationVerified'] is False
    runtime = StrategyRuntime(tmp_path)
    revision = runtime.save_strategy(restored['result']['candidates'][0])
    prices = [10, 20, 30, 40]
    data = runtime.import_data([{'date': f'2025-01-0{i+1}', 'open': price, 'high': price, 'low': price, 'close': price}
                                for i, price in enumerate(prices)], {'kind': 'imported', 'label': 'independent prices'})
    run = runtime.run(revision['id'], data['id'], {'initialCash': 100, 'feeBps': 0, 'slippageBps': 0, 'taxBps': 0})
    assert run['report']['metrics']['finalEquity'] == 120
    assert run['report']['trades'][0]['date'] == '2025-01-02'
    assert len(calls) == 1
    for path in tmp_path.rglob('*'):
        if path.is_file():
            assert b'FIXTURE_PRIVATE_KEY' not in path.read_bytes(), path


def test_new_connection_cannot_reuse_old_draft_consent(tmp_path):
    def never_send(*args):
        pytest.fail('stale consent must not send')
    session = AISession(config_factory=HttpProviderConfig, send=never_send, models=['fixture-model'], management_allowed=True)
    old = session.connect(apiKey='FIXTURE_KEY_ONE', model='fixture-model')['configuration']['connectionId']
    store = Store(tmp_path)
    with session.lease(old) as lease:
        preview = DraftController(store, lease.generate, lease.metadata).create('전략 생성')
    new = session.connect(apiKey='FIXTURE_KEY_TWO', model='fixture-model')['configuration']['connectionId']
    with pytest.raises(ValueError, match='AI_CONNECTION_CHANGED'):
        with session.lease(old):
            pass
    with session.lease(new) as lease:
        with pytest.raises(DraftError):
            DraftController(store, lease.generate, lease.metadata).generate(preview['id'], preview['promptHash'], preview['consentVersion'])
    assert DraftController(store).get(preview['id'])['state'] == 'preview'
