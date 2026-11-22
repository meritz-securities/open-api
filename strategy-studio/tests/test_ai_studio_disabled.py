"""고객용 앱(ai_studio=False)에서는 Studio 내 AI 액션이 서버에서 403으로 차단되고 capabilities.aiStudio 가 False 다.
라이브러리 기본값도 False(fail-closed)이며, AI 가 필요한 개발 픽스처만 명시적으로 ai_studio=True 를 넘긴다.
네트워크·모델·자격증명 없음."""
import http.client, json, tempfile, threading, webbrowser
from pathlib import Path

import pytest

from runtime.server import create_server
from runtime.service import StrategyRuntime


def _serve(ai_studio=None):
    kwargs = {} if ai_studio is None else {'ai_studio': ai_studio}
    service = StrategyRuntime(tempfile.mkdtemp(), catalog=None, **kwargs)
    server = create_server(service, page=b'<!doctype html><title>t</title>')
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    return server, thread


def _post(port, path, body, token=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    headers = {'Host': f'127.0.0.1:{port}', 'Origin': f'http://127.0.0.1:{port}', 'Content-Type': 'application/json'}
    if token: headers['X-Platform-Token'] = token
    conn.request('POST', path, body=json.dumps(body), headers=headers)
    resp = conn.getresponse(); data = json.loads(resp.read() or b'{}'); conn.close()
    return resp.status, data


def _session(server):
    ticket = server.issue_ticket().split('#ticket=')[1]
    status, data = _post(server.server_port, '/api/session', {'ticket': ticket})
    assert status == 200 and data['ok'], data
    return data['result']['token']


@pytest.mark.parametrize('action', ['get_ai_connection', 'connect_ai_session', 'create_ai_draft', 'generate_ai_http_improvement'])
def test_customer_app_blocks_ai_studio_actions(action):
    server, thread = _serve(ai_studio=False)
    try:
        token = _session(server)
        status, data = _post(server.server_port, '/api/platform', {'action': action, 'arguments': {}}, token)
        assert status == 403 and data['ok'] is False and data['error']['code'] == 'AI_STUDIO_DISABLED'
        status, caps = _post(server.server_port, '/api/platform', {'action': 'capabilities', 'arguments': {}}, token)
        assert status == 200 and caps['result']['aiStudio'] is False
        # 비AI 액션은 정상 동작해야 한다(기동·DSL 무영향)
        status, tpl = _post(server.server_port, '/api/platform', {'action': 'templates', 'arguments': {}}, token)
        assert status == 200 and tpl['ok'] is True
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


@pytest.mark.parametrize('action,args', [
    ('preview_ai_generation', {'requestId': 'a' * 32}),
    ('generate_ai_candidates', {'requestId': 'a' * 32, 'promptHash': 'b' * 64, 'consentVersion': 'v1'}),
])
def test_customer_app_blocks_provider_backed_ai_generation(action, args):
    server, thread = _serve(ai_studio=False)
    try:
        token = _session(server)
        status, data = _post(server.server_port, '/api/platform', {'action': action, 'arguments': args}, token)
        assert status == 403 and data['error']['code'] == 'AI_STUDIO_DISABLED'
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_library_default_is_fail_closed():
    """StrategyRuntime / create_server 기본값은 AI Studio 꺼짐. 명시 없이 만든 서버는 AI 액션을 403 으로 막는다."""
    assert StrategyRuntime(tempfile.mkdtemp(), catalog=None).ai_studio_enabled is False
    server, thread = _serve()   # ai_studio 인자 생략
    try:
        token = _session(server)
        status, caps = _post(server.server_port, '/api/platform', {'action': 'capabilities', 'arguments': {}}, token)
        assert status == 200 and caps['result']['aiStudio'] is False
        status, data = _post(server.server_port, '/api/platform', {'action': 'get_ai_connection', 'arguments': {}}, token)
        assert status == 403 and data['error']['code'] == 'AI_STUDIO_DISABLED'
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_create_server_without_service_defaults_to_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv('MERITZ_STRATEGY_HOME', str(tmp_path))
    server = create_server(page=b'', allow_market_connection=True)
    try:
        assert server.service.ai_studio_enabled is False
        assert server.service.capabilities()['aiStudio'] is False
    finally:
        server.server_close()


def test_explicit_opt_in_keeps_ai_studio_enabled():
    server, thread = _serve(ai_studio=True)
    try:
        token = _session(server)
        status, caps = _post(server.server_port, '/api/platform', {'action': 'capabilities', 'arguments': {}}, token)
        assert status == 200 and caps['result']['aiStudio'] is True
        status, data = _post(server.server_port, '/api/platform', {'action': 'get_ai_connection', 'arguments': {}}, token)
        assert status == 200 and data['ok'] is True   # 차단되지 않음(연결 미설정 상태 응답)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_mcp_studio_open_serves_ai_studio_disabled(tmp_path, monkeypatch):
    """MCP 경로(register_platform → meritz_studio_open)로 띄운 Studio 도 capabilities.aiStudio=False 다."""
    from runtime.mcp import register_platform

    class StubMCP:
        def __init__(self): self.tools = {}
        def tool(self, name, annotations=None):
            def register(fn): self.tools[name] = fn; return fn
            return register

    opened = []
    monkeypatch.setattr(webbrowser, 'open', lambda url: opened.append(url) or True)
    stub = StubMCP()
    names = register_platform(stub, None, None, StrategyRuntime(tmp_path))
    assert 'meritz_studio_open' in names
    result = stub.tools['meritz_studio_open']()
    assert result['opened'] is True and len(opened) == 1 and '#ticket=' in opened[0]
    port = int(result['url'].rsplit(':', 1)[1].rstrip('/'))
    ticket = opened[0].split('#ticket=')[1]
    status, data = _post(port, '/api/session', {'ticket': ticket})
    assert status == 200, data
    token = data['result']['token']
    status, caps = _post(port, '/api/platform', {'action': 'capabilities', 'arguments': {}}, token)
    assert status == 200 and caps['result']['aiStudio'] is False
    status, blocked = _post(port, '/api/platform', {'action': 'connect_ai_session', 'arguments': {}}, token)
    assert status == 403 and blocked['error']['code'] == 'AI_STUDIO_DISABLED'


def test_customer_entry_disables_ai_studio_by_default():
    """runtime.server.main() 이 고객용 기본값으로 ai_studio=False 를 넘기는지 소스로 고정한다."""
    src = (Path(__file__).resolve().parents[1] / 'runtime/server.py').read_text(encoding='utf-8')
    assert 'create_server(allow_market_connection=True,allow_ai_connection=False,ai_studio=False)' in src
