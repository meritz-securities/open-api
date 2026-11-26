"""Real loopback HTTP + session + drafts + HTTP adapter; generated model only."""
import asyncio
import json
import socket
import threading
import urllib.error
import urllib.request

import httpx
import pytest

from meritz import load_catalog
from runtime import ai_studio
from runtime.ai_http_provider import generate_candidates
from runtime.server import create_server
from runtime.service import StrategyRuntime

KEY='GENERATED_STUDIO_AI_KEY'


def definition():
    return {'schemaVersion':1,'kind':'dsl','name':'Generated HTTP oracle',
            'entry':{'op':'gt','left':{'type':'constant','value':1},'right':{'type':'constant','value':0}},
            'exit':{'op':'lt','left':{'type':'constant','value':1},'right':{'type':'constant','value':0}},
            'quantity':1,'allowPyramiding':False,'risk':{'stopLossPct':None,'takeProfitPct':None}}


class Body(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield json.dumps({'type':'message','role':'assistant','stop_reason':'end_turn',
                         'content':[{'type':'text','text':json.dumps({'candidates':[definition()]})}]}).encode()


class Browser:
    def __init__(self,server):
        self.server=server;self.origin=f'http://127.0.0.1:{server.server_port}';self.token=None
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def post(self,path,body,*,token=True,origin=None,host=None):
        headers={'Content-Type':'application/json','Origin':origin or self.origin}
        if token and self.token:headers['X-Platform-Token']=self.token
        if host:headers['Host']=host
        request=urllib.request.Request(self.origin+path,data=json.dumps(body).encode(),headers=headers)
        try:
            with self.opener.open(request,timeout=5) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:return error.code,json.load(error)
    def authenticate(self):
        ticket=self.server.issue_ticket().split('ticket=')[1]
        status,result=self.post('/api/session',{'ticket':ticket},token=False)
        assert status==200;self.token=result['result']['token']
    def call(self,action,args=None,ok=True):
        status,result=self.post('/api/platform',{'action':action,'arguments':args or {}})
        if ok:assert status==200 and result['ok'],result
        else:assert status==400 and not result['ok'],result
        return result['result'] if ok else result['error']


@pytest.fixture
def app(tmp_path,monkeypatch):
    original_connect=socket.socket.connect
    def restricted(sock,address):
        if not isinstance(address,tuple) or address[0]!='127.0.0.1':pytest.fail('External network forbidden')
        return original_connect(sock,address)
    monkeypatch.setattr(socket.socket,'connect',restricted)
    state={'calls':0,'entered':threading.Event(),'release':threading.Event(),'hold':False}
    async def handler(request):
        assert str(request.url)=='https://api.anthropic.com/v1/messages'
        assert request.headers['x-api-key']==KEY
        state['calls']+=1;state['entered'].set()
        if state['hold']:
            while not state['release'].is_set():await asyncio.sleep(.01)
        return httpx.Response(200,headers={'content-type':'application/json'},stream=Body())
    def send(config,prompt,cancel):
        return generate_candidates(config,prompt,cancel,_transport_factory=lambda:httpx.MockTransport(handler))
    monkeypatch.setattr(ai_studio,'generate_candidates',send)
    service=StrategyRuntime(tmp_path,catalog=load_catalog(),allow_market_connection=True,allow_ai_connection=True,ai_studio=True)
    server=create_server(service,page=b'<html>generated fixture</html>');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=Browser(server);client.authenticate()
    try:yield client,service,state,tmp_path
    finally:state['release'].set();server.shutdown();server.server_close();thread.join(2)


def configure(client):
    return client.call('connect_ai_session',{'apiKey':KEY,'model':'claude-opus-5-5','maxOutputTokens':512,'timeoutSeconds':2})['configuration']


def draft(client,config):
    return client.call('create_ai_draft',{'request':'Buy one share and hold; generated test only.','connectionId':config['connectionId']})


def approved(record):
    return {'requestId':record['id'],'promptHash':record['promptHash'],'consentVersion':record['consentVersion']}


def test_auth_origin_host_and_no_cli_dispatch(app):
    client,service,state,_=app
    args={'action':'connect_ai_session','arguments':{'apiKey':KEY,'model':'claude-opus-5-5'}}
    assert client.post('/api/platform',args,token=False)[0]==403
    assert client.post('/api/platform',args,origin='https://untrusted.invalid')[0]==403
    assert client.post('/api/platform',args,host='untrusted.invalid')[0]==403
    assert not client.call('get_ai_connection')['configured'] and state['calls']==0
    for action in ai_studio.HTTP_ACTIONS:
        with pytest.raises(ValueError,match='지원하지 않는'):service.dispatch(action,{})


def test_opt_in_and_existing_service_options(tmp_path,monkeypatch):
    shared=StrategyRuntime(tmp_path)
    assert shared._ai_studio.get_ai_connection()['managementAllowed'] is False
    with pytest.raises(ValueError,match='MANAGEMENT_DISABLED'):
        shared._ai_studio.connect_ai_session(apiKey=KEY,model='claude-opus-5-5')
    for option in ('allow_market_connection','allow_ai_connection'):
        with pytest.raises(ValueError,match='동시에'):create_server(shared,page=b'',**{option:True})
    monkeypatch.setenv('MERITZ_STRATEGY_HOME',str(tmp_path/'standalone'))
    server=create_server(page=b'',allow_market_connection=True,allow_ai_connection=True,ai_studio=True)
    try:
        status=server.service._ai_studio.get_ai_connection()
        assert status['managementAllowed'] and status['models']==['claude-opus-5-5']
        assert not status['configured'] and status['authenticated'] is None
    finally:server.server_close()


def test_config_and_preview_never_generate_or_expose_keys(app):
    client,service,state,folder=app
    assert client.call('create_ai_draft',{'request':'text','connectionId':'absent'},ok=False)['message']=='AI_CONNECTION_REQUIRED'
    for extra in ({'endpoint':'https://untrusted.invalid'},{'executable':'/tmp/no'},{'apiKey':'bad\nkey'},{'model':'unlisted-model'}):
        client.call('connect_ai_session',{'apiKey':KEY,'model':'claude-opus-5-5',**extra},ok=False)
    config=configure(client);record=draft(client,config)
    assert record['state']=='preview' and record['previewScope'].startswith('user-data-only')
    assert record['metadata']==config
    assert KEY not in json.dumps(record)+json.dumps(client.call('get_ai_connection'))
    assert state['calls']==0 and service.list_strategies()['strategies']==[] and service.list_runs()['runs']==[]
    for path in folder.rglob('*'):
        if path.is_file():assert KEY.encode() not in path.read_bytes()
    old=config;new=configure(client);assert old['connectionId']!=new['connectionId']
    assert client.call('generate_ai_draft',approved(record),ok=False)['message']=='AI_CONNECTION_CHANGED'
    assert state['calls']==0


def test_full_preview_generate_explicit_save_and_common_engine(app):
    client,service,state,folder=app
    record=draft(client,configure(client))
    client.call('generate_ai_draft',{**approved(record),'promptHash':'0'*64},ok=False)
    assert state['calls']==0
    ready=client.call('generate_ai_draft',approved(record))
    assert ready['state']=='ready' and ready['result']['candidates']==[definition()]
    assert service.list_strategies()['strategies']==[] and service.list_runs()['runs']==[]
    client.call('disconnect_ai_session')
    assert client.call('generate_ai_draft',approved(record))==ready
    assert client.call('get_ai_draft',{'requestId':record['id']})==ready and state['calls']==1
    saved=client.call('save_strategy',{'definition':ready['result']['candidates'][0]})
    bars=[{'date':f'2025-01-0{i+1}','open':p,'high':p,'low':p,'close':p} for i,p in enumerate([10,20,30,40])]
    data=client.call('import_data',{'bars':bars,'source':{'kind':'imported','label':'Generated oracle','currency':'KRW'}})
    run=client.call('run',{'revisionId':saved['id'],'datasetId':data['id'],'settings':{'initialCash':100,'feeBps':0,'slippageBps':0,'taxBps':0}})
    assert run['report']['metrics']['finalEquity']==120 and run['report']['trades'][0]['price']==20
    assert run['manifest']['dataHash']==data['contentHash']
    for path in folder.rglob('*'):
        if path.is_file():assert KEY.encode() not in path.read_bytes()


def test_persisted_preview_get_cancel_after_restart_without_key(app):
    client,_,state,folder=app
    record=draft(client,configure(client));client.call('disconnect_ai_session')
    restarted=StrategyRuntime(folder,ai_studio=True)
    server=create_server(restarted,page=b'');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    other=Browser(server);other.authenticate()
    try:
        assert not other.call('get_ai_connection')['configured']
        assert other.call('connect_ai_session',{'apiKey':KEY,'model':'claude-opus-5-5'},ok=False)['message']=='AI_CONNECTION_MANAGEMENT_DISABLED'
        assert other.call('get_ai_draft',{'requestId':record['id']})['state']=='preview'
        assert other.call('cancel_ai_draft',{'requestId':record['id']})['state']=='cancelled'
        assert other.call('generate_ai_draft',approved(record))['state']=='cancelled'
        assert state['calls']==0
    finally:server.shutdown();server.server_close();thread.join(2)


def test_active_generation_allows_http_get_cancel_but_blocks_reconfigure(app):
    client,_,state,_=app
    record=draft(client,configure(client));state['hold']=True;results=[]
    thread=threading.Thread(target=lambda:results.append(client.call('generate_ai_draft',approved(record))));thread.start()
    try:
        assert state['entered'].wait(2)
        assert client.call('get_ai_connection')['busy']
        assert client.call('get_ai_draft',{'requestId':record['id']})['state']=='generating'
        assert client.call('generate_ai_draft',approved(record))['state']=='generating'
        assert state['calls']==1
        client.call('disconnect_ai_session',ok=False)
        client.call('connect_ai_session',{'apiKey':KEY,'model':'claude-opus-5-5'},ok=False)
        client.call('cancel_ai_draft',{'requestId':record['id']})
        thread.join(3)
        assert not thread.is_alive() and results[0]['state']=='cancelled'
        assert not client.call('get_ai_connection')['busy']
    finally:state['release'].set();thread.join(3)


def test_workspace_stays_out_of_public_records_and_files(app):
    client,_,state,folder=app
    workspace='GENERATED_PRIVATE_WORKSPACE'
    status=client.call('connect_ai_session',{'apiKey':KEY,'model':'claude-opus-5-5',
                                          'workspaceId':workspace,'timeoutSeconds':5})
    record=draft(client,status['configuration'])
    ready=client.call('generate_ai_draft',approved(record))
    assert ready['state']=='ready' and state['calls']==1
    public=[status,record,ready,client.call('get_ai_connection'),
            client.call('get_ai_draft',{'requestId':record['id']}),client.call('disconnect_ai_session')]
    for secret in (KEY,workspace):
        assert secret not in json.dumps(public)
        for path in folder.rglob('*'):
            if path.is_file():assert secret.encode() not in path.read_bytes()


def test_terminal_generate_is_read_even_with_wrong_consent_after_disconnect(app):
    client,_,state,_=app
    record=draft(client,configure(client))
    ready=client.call('generate_ai_draft',approved(record))
    client.call('disconnect_ai_session')
    for consent in ({'promptHash':'incorrect','consentVersion':'incorrect'},
                    {'promptHash':'','consentVersion':''},
                    {'promptHash':None,'consentVersion':None}):
        assert client.call('generate_ai_draft',{'requestId':record['id'],**consent})==ready
    assert state['calls']==1 and not client.call('get_ai_connection')['configured']


def test_wrong_connection_and_disconnected_preview_never_invoke_provider(app):
    client,_,state,_=app
    config=configure(client)
    record=draft(client,config)
    wrong='0'*32 if config['connectionId']!='0'*32 else '1'*32
    error=client.call('create_ai_draft',{'request':'generated fixture','connectionId':wrong},ok=False)
    assert error['message']=='AI_CONNECTION_CHANGED'
    assert client.call('get_ai_connection')['configuration']==config
    client.call('disconnect_ai_session')
    assert client.call('generate_ai_draft',approved(record),ok=False)['message']=='AI_CONNECTION_REQUIRED'
    assert client.call('get_ai_draft',{'requestId':record['id']})==record
    assert state['calls']==0


@pytest.mark.parametrize('extra',['endpoint','command'])
@pytest.mark.parametrize('action',sorted(ai_studio.HTTP_ACTIONS))
def test_extra_transport_arguments_rejected_on_every_action(app,action,extra):
    client,_,state,_=app
    config=configure(client);record=draft(client,config)
    arguments={
        'get_ai_connection':{},
        'connect_ai_session':{'apiKey':KEY,'model':'claude-opus-5-5'},
        'disconnect_ai_session':{},
        'create_ai_draft':{'request':'generated fixture','connectionId':config['connectionId']},
        'get_ai_draft':{'requestId':record['id']},
        'generate_ai_draft':approved(record),
        'cancel_ai_draft':{'requestId':record['id']},
        'preview_ai_http_improvement':{'requestId':record['id'],'connectionId':config['connectionId']},
        'generate_ai_http_improvement':{'requestId':record['id'],'promptHash':'fixture','consentVersion':'fixture'},
    }[action]
    error=client.call(action,{**arguments,extra:'GENERATED_FORBIDDEN_VALUE'},ok=False)
    assert error['code']=='INVALID_REQUEST'
    assert 'GENERATED_FORBIDDEN_VALUE' not in json.dumps(error)
    assert client.call('get_ai_connection')['configuration']==config
    assert client.call('get_ai_draft',{'requestId':record['id']})==record
    assert state['calls']==0


def test_parallel_generate_calls_once_and_create_is_busy(app):
    client,_,state,_=app
    # A larger fixture deadline permits scheduling latency; events, not sleeps,
    # prove that all losing HTTP requests returned while the provider is held.
    config=client.call('connect_ai_session',{'apiKey':KEY,'model':'claude-opus-5-5',
                                           'timeoutSeconds':20})['configuration']
    record=draft(client,config);state['hold']=True
    start=threading.Barrier(5);completed=threading.Condition();results=[];errors=[]
    def generate():
        try:
            start.wait(5)
            result=client.post('/api/platform',{'action':'generate_ai_draft','arguments':approved(record)})
            with completed:results.append(result);completed.notify_all()
        except BaseException as error:
            with completed:errors.append(error);completed.notify_all()
    threads=[threading.Thread(target=generate) for _ in range(4)]
    for thread in threads:thread.start()
    try:
        start.wait(5)
        assert state['entered'].wait(3)
        with completed:
            assert completed.wait_for(lambda:len(results)+len(errors)>=3,timeout=3)
        assert not errors and len(results)==3 and state['calls']==1
        for status,body in results:
            if body['ok']:
                assert status==200 and body['result']['state']=='generating'
            else:
                assert status==400 and body['error']['message']=='AI_CONNECTION_BUSY'
        assert client.call('create_ai_draft',{'request':'second generated fixture',
                          'connectionId':config['connectionId']},ok=False)['message']=='AI_CONNECTION_BUSY'
        assert client.call('get_ai_draft',{'requestId':record['id']})['state']=='generating'
    finally:
        state['release'].set()
        for thread in threads:thread.join(5)
    assert all(not thread.is_alive() for thread in threads) and not errors
    assert len(results)==4 and state['calls']==1
    ready=[body['result'] for status,body in results if status==200 and body['result']['state']=='ready']
    assert len(ready)==1 and ready[0]['result']['candidates']==[definition()]
    assert client.call('get_ai_draft',{'requestId':record['id']})==ready[0]
    assert not client.call('get_ai_connection')['busy']


def test_http_improvement_uses_shared_engine_and_auth_boundary(app):
    client,service,state,_=app
    config=configure(client)
    revision=service.save_strategy(definition())
    data=service.import_data([{'date':f'2025-01-0{i+1}','open':p,'high':p,'low':p,'close':p} for i,p in enumerate([10,20,30,40])],{'kind':'imported','label':'fixture'})
    baseline=service.run(revision['id'],data['id'],{'initialCash':100,'feeBps':0,'slippageBps':0,'taxBps':0})
    request=client.call('create_ai_request',{'runId':baseline['id'],'purpose':'generated improvement'})['request']['id']
    preview_args={'requestId':request,'connectionId':config['connectionId']}
    for action,args in [('preview_ai_http_improvement',preview_args),('generate_ai_http_improvement',{'requestId':request,'promptHash':'unknown','consentVersion':'unknown'})]:
        body={'action':action,'arguments':args}
        assert client.post('/api/platform',body,token=False)[0]==403
        assert client.post('/api/platform',body,origin='https://invalid.test')[0]==403
    p=client.call('preview_ai_http_improvement',preview_args)
    assert state['calls']==0
    result=client.call('generate_ai_http_improvement',{'requestId':request,'promptHash':p['promptHash'],'consentVersion':p['consentVersion']})
    assert result['status']=='succeeded' and result['submission']['origin']=='local-http-generated'
    child=service.get_run(result['attempts'][0]['runId'])
    assert child['report']['metrics']['finalEquity']==120
    client.call('disconnect_ai_session')
    assert client.call('preview_ai_http_improvement',{'requestId':request})==p
    assert client.call('generate_ai_http_improvement',{'requestId':request,'promptHash':p['promptHash'],'consentVersion':p['consentVersion']})==result
    assert state['calls']==1
