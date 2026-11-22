"""R-10(b): 로컬 서버가 내부 ValueError 영문 문구를 그대로 내보내지 않고 고객 안내로 바꾼다.

한국어 문구와 대문자 코드 토큰(Studio 가 매핑하는 RESEARCH_NOT_FOUND 등)은 그대로 전달하고,
알려진 내부 문구는 고정 한국어 안내 + 전용 코드로, 그 밖의 영문 내부 문구는 일반 안내 + 코드만 남긴다.
"""
import http.client, json, threading

import pytest

from runtime.server import GENERIC_ERROR_MESSAGE, KNOWN_ERRORS, create_server, public_error
from runtime.service import StrategyRuntime


@pytest.mark.parametrize('text,code,message', [
    ('Another local run is active', 'RUN_ACTIVE', '다른 백테스트가 실행 중입니다. 완료 후 다시 시도하세요.'),
    ('Snapshot exceeds local storage limit', 'STORAGE_LIMIT', KNOWN_ERRORS['Snapshot exceeds local storage limit'][1]),
    ('Snapshot exceeds decoded storage limit', 'STORAGE_LIMIT', KNOWN_ERRORS['Snapshot exceeds local storage limit'][1]),
    ('unknown strategy kind or fields', 'INVALID_STRATEGY', '전략 정의 형식이 올바르지 않습니다.'),
    ('Snapshot not found', 'NOT_FOUND', KNOWN_ERRORS['Snapshot not found'][1]),
])
def test_known_internal_messages_are_mapped(text, code, message):
    assert public_error(ValueError(text)) == (code, message)


def test_unsupported_platform_is_mapped_to_customer_message():
    text = 'unsupported_platform: 내부 조건은 고객 응답에서 숨깁니다.'
    assert public_error(ValueError(text)) == ('UNSUPPORTED_PLATFORM', 'Python 전략 격리 환경이 준비되지 않았습니다.')


def test_webview_downloads_are_enabled_for_native_report_exports():
    from runtime.server import configure_webview_downloads

    class Webview:
        settings = {'ALLOW_DOWNLOADS': False}

    configure_webview_downloads(Webview)
    assert Webview.settings['ALLOW_DOWNLOADS'] is True


@pytest.mark.parametrize('text', ['RESEARCH_NOT_FOUND', 'TRAINING_CANDIDATES_NOT_COMPLETE', 'AI_CONNECTION_REQUIRED', 'POOL_NOT_FROZEN'])
def test_code_tokens_pass_through_for_studio_mapping(text):
    assert public_error(ValueError(text)) == ('INVALID_REQUEST', text)


@pytest.mark.parametrize('text', ['설정은 initialCash, feeBps, slippageBps, taxBps만 지원합니다.', '지원하지 않는 플랫폼 요청', '확정 일봉 2~5,000개를 입력하세요.'])
def test_korean_messages_pass_through(text):
    assert public_error(ValueError(text)) == ('INVALID_REQUEST', text)


@pytest.mark.parametrize('text,code', [
    ('research source snapshot changed', 'RESEARCH_SOURCE_SNAPSHOT_CHANGED'),
    ('Invalid provider data quality warnings', 'INVALID_PROVIDER_DATA_QUALITY_WARNINGS'),
    ('reserved research run ID is already occupied', 'RESERVED_RESEARCH_RUN_ID_IS_ALREADY_OCCUPIED'),
])
def test_unknown_english_messages_become_generic_with_code_only(text, code):
    public_code, message = public_error(ValueError(text))
    assert public_code == code and message == GENERIC_ERROR_MESSAGE
    assert text not in message


def test_very_long_english_message_code_is_bounded():
    code, message = public_error(ValueError('x' * 2000 + ' internal detail'))
    assert len(code) <= 64 and message == GENERIC_ERROR_MESSAGE


# ---- HTTP end-to-end ---------------------------------------------------------

def _post(port, path, body, token=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    headers = {'Host': f'127.0.0.1:{port}', 'Origin': f'http://127.0.0.1:{port}', 'Content-Type': 'application/json'}
    if token: headers['X-Platform-Token'] = token
    conn.request('POST', path, body=json.dumps(body), headers=headers)
    resp = conn.getresponse(); data = json.loads(resp.read() or b'{}'); conn.close()
    return resp.status, data


@pytest.fixture
def served(tmp_path):
    service = StrategyRuntime(tmp_path, catalog=None)
    server = create_server(service, page=b'<html></html>')
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True); thread.start()
    ticket = server.issue_ticket().split('#ticket=')[1]
    status, data = _post(server.server_port, '/api/session', {'ticket': ticket}); assert status == 200
    try:
        yield service, server.server_port, data['result']['token']
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_http_active_run_lock_and_not_found_are_korean(served):
    service, port, token = served
    definition = service.templates()['templates'][0]
    revision = service.save_strategy(definition)
    dataset = service.demo_data()
    status, data = _post(port, '/api/platform', {'action': 'get_run', 'arguments': {'runId': 'f' * 32}}, token)
    assert status == 400 and data['error'] == {'code': 'NOT_FOUND', 'message': KNOWN_ERRORS['Snapshot not found'][1]}
    with service.store.execution_lock():       # 같은 저장소의 다른 실행이 엔진 잠금을 쥔 상태
        status, data = _post(port, '/api/platform', {'action': 'run', 'arguments': {'revisionId': revision['id'], 'datasetId': dataset['id']}}, token)
    assert status == 400 and data['error']['code'] == 'RUN_ACTIVE'
    assert data['error']['message'] == '다른 백테스트가 실행 중입니다. 완료 후 다시 시도하세요.'
    assert 'Another local run' not in json.dumps(data)


def test_http_english_internal_message_is_not_exposed(served, monkeypatch):
    service, port, token = served
    monkeypatch.setattr(service, 'list_strategies', lambda: (_ for _ in ()).throw(ValueError('internal english detail leaked')))
    status, data = _post(port, '/api/platform', {'action': 'list_strategies', 'arguments': {}}, token)
    assert status == 400 and data['error'] == {'code': 'INTERNAL_ENGLISH_DETAIL_LEAKED', 'message': GENERIC_ERROR_MESSAGE}


def test_http_code_token_and_korean_messages_unchanged(served):
    service, port, token = served
    status, data = _post(port, '/api/platform', {'action': 'get_research', 'arguments': {'researchId': 'a' * 32}}, token)
    assert status == 400 and data['error'] == {'code': 'INVALID_REQUEST', 'message': 'RESEARCH_NOT_FOUND'}
    status, data = _post(port, '/api/platform', {'action': 'not_an_action', 'arguments': {}}, token)
    assert status == 400 and data['error'] == {'code': 'INVALID_REQUEST', 'message': '지원하지 않는 플랫폼 요청'}
    status, data = _post(port, '/api/platform', {'action': 'templates', 'arguments': {'extra': 1}}, token)
    assert status == 400 and data['error'] == {'code': 'INVALID_REQUEST', 'message': '인자 형식을 확인하세요.'}
