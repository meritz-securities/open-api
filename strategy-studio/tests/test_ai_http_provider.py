"""Generated responses only; no credentials or external network."""
import asyncio
from dataclasses import replace
import json
import socket
import threading
import time

import httpx
import pytest

from runtime import ai_http_provider as provider
from runtime.ai_http_provider import HttpProviderConfig, generate_candidates, ProviderError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('External network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    yield
    # Bound test cleanup without leaving a daemon between independent cases.
    limit = time.monotonic() + 2
    while provider._GATE.locked() and time.monotonic() < limit:
        time.sleep(.005)
    assert not provider._GATE.locked()


@pytest.fixture
def config():
    return HttpProviderConfig(api_key='GENERATED_KEY', model='explicit-model', timeout_seconds=2)


def definition():
    return {'schemaVersion': 1, 'kind': 'dsl', 'name': 'Generated condition',
            'entry': {'op': 'gt', 'left': {'type': 'field', 'field': 'close'},
                      'right': {'type': 'constant', 'value': 10}},
            'exit': {'op': 'lt', 'left': {'type': 'field', 'field': 'close'},
                     'right': {'type': 'constant', 'value': 5}},
            'quantity': 1, 'allowPyramiding': False,
            'risk': {'stopLossPct': None, 'takeProfitPct': None}}


def envelope(**changes):
    return {'type': 'message', 'role': 'assistant', 'stop_reason': 'end_turn',
            'stop_details': None, 'content': [{'type': 'text', 'text': json.dumps({'candidates': [definition()]})}], **changes}


class Bytes(httpx.AsyncByteStream):
    def __init__(self, raw): self.raw = raw
    async def __aiter__(self): yield self.raw


def transport(value=None, *, raw=None, status=200, headers=None, observed=None):
    raw = raw if raw is not None else json.dumps(value if value is not None else envelope()).encode()
    async def handler(request):
        if observed is not None: observed.append(request)
        return httpx.Response(status, headers={'content-type': 'application/json', **(headers or {})}, stream=Bytes(raw))
    return lambda: httpx.MockTransport(handler)


def failure(config, factory, expected, prompt='private prompt'):
    with pytest.raises(ProviderError) as caught:
        generate_candidates(config, prompt, _transport_factory=factory)
    assert str(caught.value) == expected
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert 'private' not in repr(caught.value) and 'GENERATED_KEY' not in repr(caught.value)


def test_exact_one_post_explicit_config_and_no_proxy_discovery(config, monkeypatch):
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.setenv(key, 'http://untrusted.invalid:9')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'DO_NOT_READ_ENV_KEY')
    seen = []
    result = generate_candidates(replace(config, workspace_id='wrkspc_generated'), 'close above ten', _transport_factory=transport(observed=seen))
    assert result == [definition()]
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == 'https://api.anthropic.com/v1/messages' and request.method == 'POST'
    assert request.headers['x-api-key'] == 'GENERATED_KEY'
    assert request.headers['anthropic-version'] == '2026-10-21'
    assert request.headers['anthropic-workspace-id'] == 'wrkspc_generated'
    assert request.headers['accept-encoding'] == 'identity'
    body = json.loads(request.content)
    assert set(body) == {'model', 'max_tokens', 'stream', 'system', 'messages'}
    assert body['model'] == 'explicit-model' and body['max_tokens'] == 4096 and body['stream'] is False
    assert body['messages'] == [{'role': 'user', 'content': 'close above ten'}]
    assert 'output_config' not in body and 'tools' not in body and 'GENERATED_KEY' not in request.content.decode()
    assert 'GENERATED_KEY' not in repr(config)


@pytest.mark.parametrize('field,value', [('api_key',''),('api_key','bad\nkey'),('api_key','한글'),('model',''),('model','a/b'),('workspace_id','x\r'),('max_tokens',True),('max_tokens',8193),('timeout_seconds',float('nan')),('timeout_seconds',0),('response_limit',1_000_001)])
def test_bad_config_never_starts(config,field,value):
    failure(replace(config, **{field:value}), lambda: pytest.fail('must not start'), 'PROVIDER_CONFIG_INVALID')


@pytest.mark.parametrize('prompt', ['', '\ud800', 'x'*120_001, None])
def test_bad_prompt_has_no_exception_context(config,prompt):
    failure(config, lambda: pytest.fail('must not start'), 'PROVIDER_INVALID_PROMPT', prompt)


@pytest.mark.parametrize('reason,code', [('refusal','PROVIDER_REFUSED'),('max_tokens','PROVIDER_TRUNCATED'),('model_context_window_exceeded','PROVIDER_TRUNCATED'),('tool_use','PROVIDER_INVALID_OUTPUT'),('pause_turn','PROVIDER_INVALID_OUTPUT'),('stop_sequence','PROVIDER_INVALID_OUTPUT'),(None,'PROVIDER_INVALID_OUTPUT')])
def test_stop_reasons_fail_closed(config,reason,code):
    failure(config, transport(envelope(stop_reason=reason)), code)


def test_refusal_details_even_with_inconsistent_end_turn(config):
    failure(config, transport(envelope(stop_details={'type':'refusal','explanation':'private response'})), 'PROVIDER_REFUSED')


@pytest.mark.parametrize('raw', [b'not json private response', b'\xff', b'{"type":"message","type":"message"}', b'{"unused":NaN}', b'{"unused":1e999}', b'['*2000+b']'*2000])
def test_bad_envelope(config,raw):
    failure(config, transport(raw=raw), 'PROVIDER_INVALID_OUTPUT')


@pytest.mark.parametrize('content', [[], [{'type':'tool_use','name':'private tool'}], [{'type':'text','text':'{}'},{'type':'text','text':'{}'}], [{'type':'text','text':'```json\n{}\n```'}], [{'type':'text','text':'{"candidates":[],"extra":1}'}], [{'type':'text','text':'{"candidates":[],"candidates":[]}'}], [{'type':'text','text':'{"candidates":[NaN]}'}]])
def test_nontext_or_invalid_output(config,content):
    failure(config, transport(envelope(content=content)), 'PROVIDER_INVALID_OUTPUT')


@pytest.mark.parametrize('change', [{'kind':'python','code':'print(1)'}, {'quantity':-1}, {'entry':{'op':'unsupported'}}, {'unexpected':1}, {'quantity':float('inf')}])
def test_independent_dsl_validation(config,change):
    candidate={**definition(),**change}
    failure(config, transport(envelope(content=[{'type':'text','text':json.dumps({'candidates':[candidate]})}])), 'PROVIDER_INVALID_OUTPUT')


@pytest.mark.parametrize('count',[0,4])
def test_candidate_bounds(config,count):
    failure(config,transport(envelope(content=[{'type':'text','text':json.dumps({'candidates':[definition()]*count})}])), 'PROVIDER_INVALID_OUTPUT')


@pytest.mark.parametrize('status',[301,307,400,401,429,500])
def test_http_errors_no_follow_retry_or_echo(config,status):
    seen=[]
    failure(config, transport(raw=b'private response GENERATED_KEY',status=status,headers={'location':'https://untrusted.invalid'},observed=seen),'PROVIDER_HTTP_ERROR')
    assert len(seen)==1


@pytest.mark.parametrize('headers,code',[({'content-encoding':'gzip'},'PROVIDER_INVALID_OUTPUT'),({'content-type':'text/html'},'PROVIDER_INVALID_OUTPUT'),({'content-length':'9999999999'},'PROVIDER_OUTPUT_LIMIT'),({'content-length':'bad'},'PROVIDER_INVALID_OUTPUT')])
def test_response_headers(config,headers,code):
    failure(config,transport(headers=headers),code)


def test_raw_size_limit_and_chunked_response(config):
    failure(replace(config,response_limit=30),transport(raw=b'x'*31),'PROVIDER_OUTPUT_LIMIT')
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'x'*20
            yield b'y'*20
    failure(replace(config,response_limit=30),lambda:httpx.MockTransport(lambda _:httpx.Response(200,headers={'content-type':'application/json'},stream=Chunks())),'PROVIDER_OUTPUT_LIMIT')


def test_pre_cancel_has_no_transport(config):
    event=threading.Event();event.set()
    with pytest.raises(ProviderError,match='PROVIDER_CANCELLED'):
        generate_candidates(config,'prompt',event,_transport_factory=lambda:pytest.fail('must not start'))


@pytest.mark.parametrize('stage',['headers','body'])
@pytest.mark.parametrize('mode',['cancel','deadline'])
def test_pending_io_cancel_deadline_and_cleanup(config,stage,mode):
    started=threading.Event();closed=threading.Event();cancel=threading.Event()
    async def hang():
        started.set()
        try:await asyncio.Event().wait()
        finally:closed.set()
    class Hanging(httpx.AsyncByteStream):
        async def __aiter__(self):
            await hang()
            yield b''
    async def handler(_):
        if stage=='headers':await hang()
        return httpx.Response(200,headers={'content-type':'application/json'},stream=Hanging())
    if mode=='cancel':
        def trigger():
            assert started.wait(2)
            cancel.set()
        thread=threading.Thread(target=trigger);thread.start()
    before=time.monotonic()
    with pytest.raises(ProviderError,match='PROVIDER_CANCELLED' if mode=='cancel' else 'PROVIDER_TIMEOUT'):
        generate_candidates(replace(config,timeout_seconds=.2 if mode=='deadline' else 2),'prompt',cancel,_transport_factory=lambda:httpx.MockTransport(handler))
    assert time.monotonic()-before < 1
    assert started.is_set() and closed.wait(1)
    if mode=='cancel':thread.join(1)


def test_noncooperative_cleanup_keeps_gate_but_caller_is_bounded(config):
    entered=threading.Event();release=threading.Event()
    # Simulate an uninterruptible resolver/factory; no busy loops/network.
    def stuck():
        entered.set();release.wait(3)
        return httpx.MockTransport(lambda _:pytest.fail('cancelled before request'))
    try:
        before=time.monotonic()
        failure(replace(config,timeout_seconds=.15),stuck,'PROVIDER_TIMEOUT')
        assert entered.is_set() and time.monotonic()-before < 1
        failure(config,lambda:pytest.fail('gate must refuse'),'PROVIDER_BUSY')
    finally:release.set()


def test_network_error_does_not_retain_request(config):
    async def fail(request):raise httpx.ConnectError('private response GENERATED_KEY',request=request)
    failure(config,lambda:httpx.MockTransport(fail),'PROVIDER_FAILED')


def test_default_transport_explicitly_disables_environment_and_retries(config,monkeypatch):
    options=[]
    def factory(**kwargs):
        options.append(kwargs)
        return transport()()
    monkeypatch.setattr(provider.httpx,'AsyncHTTPTransport',factory)
    assert generate_candidates(config,'prompt') == [definition()]
    assert options == [{'retries':0,'trust_env':False}]


def test_optional_workspace_is_omitted(config):
    seen=[]
    generate_candidates(config,'prompt',_transport_factory=transport(observed=seen))
    assert 'anthropic-workspace-id' not in seen[0].headers
    assert 'authorization' not in seen[0].headers


def test_response_closed_when_size_limit_fails(config):
    closed=threading.Event()
    class Large(Bytes):
        async def aclose(self):closed.set()
    failure(replace(config,response_limit=5),lambda:httpx.MockTransport(lambda _:httpx.Response(200,headers={'content-type':'application/json'},stream=Large(b'x'*6))),'PROVIDER_OUTPUT_LIMIT')
    assert closed.is_set()


def test_untrusted_providererror_is_not_echoed(config):
    async def fail(_):raise ProviderError('private response GENERATED_KEY')
    failure(config,lambda:httpx.MockTransport(fail),'PROVIDER_FAILED')


def test_no_success_after_cancellation_arrives_with_response(config):
    cancel=threading.Event()
    async def handler(_):
        cancel.set()
        return httpx.Response(200,headers={'content-type':'application/json'},stream=Bytes(json.dumps(envelope()).encode()))
    with pytest.raises(ProviderError,match='PROVIDER_CANCELLED'):
        generate_candidates(config,'prompt',cancel,_transport_factory=lambda:httpx.MockTransport(handler))


@pytest.mark.parametrize('stage',['headers','body'])
@pytest.mark.parametrize('interruption',[KeyboardInterrupt,SystemExit])
def test_caller_baseexception_aborts_io_and_preserves_exception(config,stage,interruption):
    started=threading.Event();closed=threading.Event()
    interrupt=interruption('fixture interruption')
    class InterruptCaller(threading.Event):
        def is_set(self):
            if threading.current_thread() is threading.main_thread() and started.is_set():
                raise interrupt
            return super().is_set()
    async def hang():
        started.set()
        try:await asyncio.Event().wait()
        finally:closed.set()
    class Hanging(httpx.AsyncByteStream):
        async def __aiter__(self):
            await hang()
            yield b''
    async def handler(request):
        if stage=='headers':await hang()
        return httpx.Response(200,headers={'content-type':'application/json'},stream=Hanging())
    before=time.monotonic()
    with pytest.raises(interruption) as caught:
        generate_candidates(config,'prompt',InterruptCaller(),_transport_factory=lambda:httpx.MockTransport(handler))
    assert caught.value is interrupt
    assert time.monotonic()-before<1
    assert closed.wait(1), 'worker must be cancelled before its normal 2s deadline'
