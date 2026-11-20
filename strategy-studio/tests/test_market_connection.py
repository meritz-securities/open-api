"""Memory-only credential setup; all quote clients are fixtures, no live auth."""
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from meritz import load_catalog
from meritz.accounts import Registry
from meritz.config import PROD, Settings
from runtime.server import create_server
from runtime.service import StrategyRuntime

KEY='FAKE_SESSION_KEY'
SECRET='FAKE_SESSION_SECRET'


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    import requests
    monkeypatch.setattr(requests.Session,'request',lambda *a,**k:pytest.fail('live network forbidden'))


def service(tmp_path):
    return StrategyRuntime(tmp_path,catalog=load_catalog(),allow_market_connection=True)


def test_connect_memory_only_fixed_settings_and_restart(tmp_path,monkeypatch):
    from meritz import config
    monkeypatch.setattr(config,'_load_env_file',lambda:pytest.fail('no env file reads'))
    monkeypatch.setenv('MERITZ_BASE_URL','https://untrusted.invalid')
    monkeypatch.setenv('MERITZ_READ_ONLY','0')
    env=dict(os.environ)
    runtime=service(tmp_path)
    assert runtime.get_market_connection()['configured'] is False
    result=runtime.connect_market_session(KEY,SECRET)
    assert result['configured'] is True and result['authenticated'] is None
    assert result['lastRequest'] is None
    registry=runtime._market_connection._registry
    assert registry.default.base_url==PROD and registry.default.read_only is True
    assert registry.default.app_key==KEY
    assert dict(os.environ)==env
    assert KEY not in json.dumps(result) and SECRET not in repr(registry.default)
    assert service(tmp_path).get_market_connection()['configured'] is False
    runtime.disconnect_market_session()
    assert runtime.get_market_connection()['configured'] is False
    for path in tmp_path.rglob('*'):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()
            assert SECRET.encode() not in path.read_bytes()


def test_shared_registry_unchanged_and_public_dispatch_denied(tmp_path):
    registry=Registry([Settings(label='existing',app_key='EXISTING',app_secret='EXISTING_SECRET')])
    runtime=StrategyRuntime(tmp_path,registry=registry)
    assert runtime.get_market_connection()['managementAllowed'] is False
    assert runtime.get_market_connection()['configured'] is True
    with pytest.raises(ValueError):runtime.connect_market_session(KEY,SECRET)
    with pytest.raises(ValueError):runtime.disconnect_market_session()
    assert runtime.registry is registry and registry.default.app_key=='EXISTING'
    for action in ('get_market_connection','connect_market_session','disconnect_market_session'):
        with pytest.raises(ValueError,match='지원하지 않는'):
            runtime.dispatch(action,{'appKey':KEY,'appSecret':SECRET})
    with pytest.raises(ValueError):
        StrategyRuntime(tmp_path,registry=registry,allow_market_connection=True)


@pytest.mark.parametrize('value',[None,False,'','space key','x\n','x'*4097])
def test_invalid_secret_has_fixed_error(tmp_path,value):
    runtime=service(tmp_path)
    with pytest.raises(ValueError,match='키와 시크릿은'):
        runtime.connect_market_session(value,SECRET)
    assert not runtime.get_market_connection()['configured']


def test_fetch_pins_registry_and_blocks_replacement(tmp_path,monkeypatch):
    from meritz.client import ApiClient
    from test_platform_service import definition
    runtime=service(tmp_path);runtime.connect_market_session(KEY,SECRET)
    started=threading.Event();release=threading.Event();observed=[]
    def fake_call(client,api,params):
        observed.append(client.s.app_key)
        started.set();assert release.wait(5)
        return {'ok':True,'body':{'data':[
            {'date':'20250102','oprc':'100','hprc':'110','lprc':'90','stck_clpr':'105','acml_vol':'10'},
            {'date':'20250103','oprc':'105','hprc':'115','lprc':'95','stck_clpr':'110','acml_vol':'20'}]}}
    monkeypatch.setattr(ApiClient,'call',fake_call)
    results=[];errors=[]
    def fetch():
        try:results.append(runtime.fetch_data('005930','2025-01-02','2025-01-03'))
        except Exception as error:errors.append(error)
    thread=threading.Thread(target=fetch);thread.start()
    try:
        assert started.wait(5)
        assert runtime.get_market_connection()['busy'] is True
        with pytest.raises(ValueError,match='조회 중'):runtime.connect_market_session('OTHER','OTHER_SECRET')
        with pytest.raises(ValueError,match='조회 중'):runtime.disconnect_market_session()
        with pytest.raises(ValueError,match='진행 중'):runtime.fetch_data('005930','2025-01-02','2025-01-03')
    finally:release.set();thread.join(5)
    assert not errors,errors
    assert observed==[KEY]
    assert runtime.get_market_connection()['lastRequest']['status']=='succeeded'
    assert runtime.get_market_connection()['authenticated'] is None
    dataset=results[0]
    run=runtime.run(runtime.save_strategy(definition())['id'],dataset['id'])
    assert run['manifest']['dataHash']==dataset['contentHash']
    assert KEY not in json.dumps(run) and SECRET not in json.dumps(dataset)
    runtime.connect_market_session('OTHER','OTHER_SECRET')
    assert runtime.get_market_connection()['lastRequest'] is None


def test_failure_does_not_echo_provider_secret(tmp_path,monkeypatch):
    from meritz.client import ApiClient
    runtime=service(tmp_path);runtime.connect_market_session(KEY,SECRET)
    def fail(*a,**kw):raise ValueError(SECRET)
    monkeypatch.setattr(ApiClient,'call',fail)
    with pytest.raises(ValueError) as error:runtime.fetch_data('005930','2025-01-02','2025-01-03')
    assert SECRET not in str(error.value)
    status=runtime.get_market_connection()
    assert status['lastRequest']['errorCode']=='MARKET_FETCH_FAILED'
    assert status['authenticated'] is None and status['busy'] is False


def test_authenticated_http_only_and_no_secret_response(tmp_path,capsys):
    runtime=service(tmp_path)
    server=create_server(runtime,page=b'<html></html>')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def post(path,body,token=None,origin_header=origin):
        headers={'Content-Type':'application/json','Origin':origin_header}
        if token:headers['X-Platform-Token']=token
        request=urllib.request.Request(origin+path,data=json.dumps(body).encode(),headers=headers)
        try:
            with opener.open(request) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:return error.code,json.load(error)
    args={'action':'connect_market_session','arguments':{'appKey':KEY,'appSecret':SECRET}}
    try:
        assert post('/api/platform',args)[0]==403
        for action in ('get_market_connection','disconnect_market_session'):
            assert post('/api/platform',{'action':action,'arguments':{}})[0]==403
            assert post('/api/platform',{'action':action,'arguments':{}},'invalid-token')[0]==403
        ticket=server.issue_ticket().split('ticket=')[1]
        token=post('/api/session',{'ticket':ticket})[1]['result']['token']
        assert post('/api/platform',args,token,origin_header='https://untrusted.invalid')[0]==403
        code,result=post('/api/platform',args,token)
        assert code==200 and result['result']['configured'] is True
        assert KEY not in json.dumps(result) and SECRET not in json.dumps(result)
        assert post('/api/platform',{**args,'arguments':{**args['arguments'],'base_url':'https://untrusted.invalid'}},token)[0]==400
        assert post('/api/platform',{'action':'disconnect_market_session','arguments':{}},token)[0]==200
    finally:server.shutdown();server.server_close();thread.join(5)
    logs=capsys.readouterr()
    assert KEY not in logs.out+logs.err and SECRET not in logs.out+logs.err


def test_standalone_opt_in_never_loads_environment_profiles(tmp_path,monkeypatch):
    import meritz.accounts
    import runtime.server
    monkeypatch.setattr(meritz.accounts,'load_profiles',lambda:pytest.fail('must not discover credentials'))
    original=runtime.server.StrategyRuntime
    monkeypatch.setattr(runtime.server,'StrategyRuntime',lambda **kwargs:original(tmp_path,**kwargs))
    server=create_server(page=b'<html></html>',allow_market_connection=True)
    try:
        assert server.service.get_market_connection()['managementAllowed'] is True
        assert server.service.get_market_connection()['configured'] is False
    finally:server.server_close()


def test_disconnect_does_not_revoke_and_invalid_replacement_preserves_connection(tmp_path,monkeypatch):
    from meritz.client import TokenManager
    runtime=service(tmp_path);runtime.connect_market_session(KEY,SECRET)
    monkeypatch.setattr(TokenManager,'get',lambda *a:pytest.fail('no authentication or revoke'))
    with pytest.raises(ValueError):runtime.connect_market_session('\ud800',SECRET)
    assert runtime._market_connection._registry.default.app_key==KEY
    assert runtime.disconnect_market_session()['configured'] is False


@pytest.mark.parametrize('account',['other','',False])
def test_session_account_argument_is_neutral_and_never_calls(tmp_path,account):
    runtime=service(tmp_path);runtime.connect_market_session(KEY,SECRET)
    with pytest.raises(ValueError,match='앱 전용 연결') as error:
        runtime.fetch_data('005930','2025-01-02','2025-01-03',account=account)
    assert 'MCP' not in str(error.value)
    assert runtime.get_market_connection()['lastRequest'] is None


def test_local_validation_is_processing_failure_not_auth_evidence(tmp_path):
    runtime=service(tmp_path);runtime.connect_market_session(KEY,SECRET)
    with pytest.raises(ValueError,match='종목 코드'):
        runtime.fetch_data('BAD','2025-01-02','2025-01-03')
    state=runtime.get_market_connection()
    assert state['configured'] is True and state['authenticated'] is None
    assert state['lastRequest']['errorCode']=='MARKET_FETCH_FAILED'


def test_main_explicit_opt_in_and_conflicting_service_rejected(tmp_path,monkeypatch):
    import runtime.server
    calls=[]
    class FakeServer:
        server_port=1234
        def issue_ticket(self):return 'http://127.0.0.1:1234/#ticket=fixture'
        def serve_forever(self,**kwargs):raise KeyboardInterrupt()
        def server_close(self):calls.append('closed')
    original=runtime.server.create_server
    def create(**kwargs):
        calls.append(kwargs)
        return FakeServer()
    monkeypatch.setattr(runtime.server,'create_server',create)
    runtime.server.main(open_browser=False)
    # 고객용 엔트리(2026-11-16 결정): 시세 연결은 앱 안에서, Studio 내 상용 AI 연결·생성은 끈다(ai_studio=False). AI 는 메리츠 MCP 경로만.
    assert calls==[{'allow_market_connection':True,'allow_ai_connection':False,'ai_studio':False},'closed']
    with pytest.raises(ValueError,match='동시에 지정'):
        original(service(tmp_path),page=b'<html></html>',allow_market_connection=True)


def test_us_provider_error_cannot_disclose_secrets(tmp_path,monkeypatch):
    from meritz.client import ApiClient, MeritzError
    runtime=service(tmp_path);runtime.connect_market_session(KEY,SECRET)
    calls=[]
    def fail(*args,**kwargs):
        calls.append(True)
        raise MeritzError(KEY+SECRET,body={'secret':SECRET},code='AUTH_FAILED')
    monkeypatch.setattr(ApiClient,'call',fail)
    with pytest.raises(ValueError) as error:
        runtime.fetch_data('AAPL','2025-01-02','2025-01-03',market='US',exchange='OQ',quoteMode='0')
    assert calls
    assert KEY not in str(error.value) and SECRET not in str(error.value)
    assert SECRET not in json.dumps(runtime.get_market_connection())
