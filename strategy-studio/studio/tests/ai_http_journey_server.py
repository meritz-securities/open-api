"""Real Studio HTTP/session/Store/engine. Only model wire + Meritz call are fake.
No real AI invocation, credentials, profile, live prices, orders or external network.
argv[1] is the built page. stdin accepts one JSON {action,...} per line and returns
one JSON line. Commands: ticket/restart/inspect/counters/state/release/stop,
draft(requestId), request(requestId), run(runId), mcp_replay(runId),
engine_hold/engine_release. inspect accepts optional requestId, draftId and runId.
state.value patches boolean fixture switches; response-loss switches fire once
AFTER the actual controller returned. Restart requires no active generation.
All keys below are generated test literals, never customer credentials.
"""
import asyncio
import copy
import json
import socket
import sys
import tempfile
import threading
from pathlib import Path
import httpx
import requests
from meritz.client import ApiClient
from meritz import load_catalog
from runtime import ai_studio
from runtime.ai_http_provider import generate_candidates
from runtime.server import create_server
from runtime.service import StrategyRuntime
from runtime.storage import digest
from runtime.mcp import register_platform
from fastmcp import FastMCP, Client

def no_secrets_in_files(root, secrets):
    for path in root.rglob('*'):
        try:
            if not path.is_file():
                continue
            content = path.read_bytes()
        except FileNotFoundError:
            # SQLite can remove a temporary file between listing and reading it.
            continue
        if any(secret in content for secret in secrets):
            return False
    return True


KEY='GENERATED_HTTP_JOURNEY_TEST_KEY'
SECRETS=(KEY,'generated-market-key','generated-market-secret')
stats={'modelWireRequests':0,'initialModelCalls':0,'improvementModelCalls':0,'marketFixtureCalls':0,'outboundAttempts':0,'mcpReplays':0,'doctorWireChecks':0}
state={'hold':False,'holdImprovement':False,'fail':False,'loseCreate':False,'loseGenerate':False,'loseImprovement':False}
release=threading.Event()
def forbidden(*args,**kwargs):
    stats['outboundAttempts']+=1
    raise RuntimeError('External network forbidden in fixture')
socket.socket.connect=forbidden
requests.Session.request=forbidden

def market_call(client,api,params,*args,**kwargs):
    assert api.key=='market_candles_days' and api.method=='GET'
    assert client.s.app_key=='generated-market-key' and client.s.app_secret=='generated-market-secret'
    assert client.s.read_only and params['iscd']=='005930'
    stats['marketFixtureCalls']+=1
    return {'ok':True,'verified':False,'body':{'data':[{'date':f'2025010{i+1}','oprc':str(p),'hprc':str(p),'lprc':str(p),'stck_clpr':str(p),'acml_vol':'1'} for i,p in enumerate([10,20,30,40])]}}
ApiClient.call=market_call
DEFINITION={'schemaVersion':1,'kind':'dsl','name':'시험용 AI 초안 · 가짜 모델 응답','entry':{'op':'gt','left':{'type':'constant','value':1},'right':{'type':'constant','value':0}},'exit':{'op':'lt','left':{'type':'constant','value':1},'right':{'type':'constant','value':0}},'quantity':1,'allowPyramiding':False,'risk':{'stopLossPct':None,'takeProfitPct':None}}
class Body(httpx.AsyncByteStream):
    def __init__(self,candidates):self.candidates=candidates
    async def __aiter__(self):
        yield json.dumps({'type':'message','role':'assistant','stop_reason':'end_turn','content':[{'type':'text','text':json.dumps({'candidates':self.candidates})}]}).encode()
async def model_handler(request):
    assert str(request.url)=='https://api.anthropic.com/v1/messages'
    assert request.headers['x-api-key']==KEY
    body=json.loads(request.content)
    assert all(secret not in request.content.decode() for secret in SECRETS)
    assert 'tools' not in body
    prompt=json.loads(body['messages'][0]['content'])
    initial=prompt.get('task')=='Create 1 to 3 initial DSL strategy drafts. No execution.'
    if not initial:
        assert set(prompt)=={'purpose','definition','metrics','diagnostics','instruction'}
        doctor=prompt['diagnostics']
        assert doctor['ruleVersion']=='doctor-v1' and len(doctor['items'])==12
        items={row['id']:row for row in doctor['items']}
        assert items['sample-size']['evidence']=={'sellFillCount':0}
        assert items['cash-depletion']['evidence']=={'minimumCashPct':80.0}
        assert items['drawdown']['evidence']=={'maxDrawdownPct':0.0}
        assert all(set(row)=={'id','status','title','message','evidence','threshold'} for row in doctor['items'])
        assert all(value is None or type(value) in (int,float) for row in doctor['items'] for value in row['evidence'].values())
        stats['doctorWireChecks']+=1
    stats['modelWireRequests']+=1
    stats['initialModelCalls' if initial else 'improvementModelCalls']+=1
    while (state['hold'] or state['holdImprovement'] and not initial) and not release.is_set():await asyncio.sleep(.01)
    if state['fail']:return httpx.Response(401,json={'error':{'message':'generated failure'}})
    generated=[copy.deepcopy(DEFINITION)]
    if not initial:
        two=copy.deepcopy(DEFINITION);two.update(name='시험용 개선 · 2주',quantity=2)
        none=copy.deepcopy(DEFINITION);none.update(name='시험용 개선 · 거래 없음')
        none['entry']=copy.deepcopy(DEFINITION['exit'])
        generated=[two,none]
    return httpx.Response(200,headers={'content-type':'application/json'},stream=Body(generated))
def send(config,prompt,cancel):
    return generate_candidates(config,prompt,cancel,_transport_factory=lambda:httpx.MockTransport(model_handler))
ai_studio.generate_candidates=send

with tempfile.TemporaryDirectory(prefix='ai-http-journey-') as temp:
    root=Path(temp)
    def start():
        service=StrategyRuntime(root,catalog=load_catalog(),allow_market_connection=True,allow_ai_connection=True,ai_studio=True)
        original_create=service._ai_studio.create_ai_draft;original_generate=service._ai_studio.generate_ai_draft
        original_improvement=service._ai_studio.generate_ai_http_improvement
        def create(**args):
            result=original_create(**args)
            if state['loseCreate']:
                state['loseCreate']=False
                raise ValueError('FIXTURE_RESPONSE_LOST')
            return result
        def generate(**args):
            result=original_generate(**args)
            if state['loseGenerate']:
                state['loseGenerate']=False
                raise ValueError('FIXTURE_RESPONSE_LOST')
            return result
        def improve(**args):
            result=original_improvement(**args)
            if state['loseImprovement']:
                state['loseImprovement']=False
                raise ValueError('FIXTURE_RESPONSE_LOST')
            return result
        service._ai_studio.create_ai_draft=create;service._ai_studio.generate_ai_draft=generate
        service._ai_studio.generate_ai_http_improvement=improve
        server=create_server(service,page=Path(sys.argv[1]).read_bytes())
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        return service,server,thread
    service,server,thread=start()
    engine_hold=None
    async def mcp_replay(run_id):
        baseline=service.get_run(run_id);before=digest(baseline);calls=stats['modelWireRequests']
        mcp=FastMCP('generated HTTP journey fixture')
        register_platform(mcp,None,None,service)
        async with Client(mcp) as client:
            result=(await client.call_tool('meritz_platform_backtest',{'revisionId':baseline['revisionId'],
                'datasetId':baseline['datasetId'],'settings':baseline['settings']})).structured_content
        assert stats['modelWireRequests']==calls
        assert digest(service.get_run(run_id))==before
        assert result['manifest']['resultHash']==baseline['manifest']['resultHash']
        stats['mcpReplays']+=1
        return {'sameResultHash':True,'originalUnchanged':True,'modelCallsUnchanged':True,
                'runId':result['id'],'resultHash':result['manifest']['resultHash'],
                'originalResultHash':baseline['manifest']['resultHash']}
    print(json.dumps({'url':server.issue_ticket(),'fixture':'generated model wire + generated Meritz prices; no live model or quotes',
                      'modelKey':KEY,'marketKey':SECRETS[1],'marketSecret':SECRETS[2]}),flush=True)
    try:
        for line in sys.stdin:
            message=json.loads(line);action=message['action'];result={}
            if action=='stop':
                print(json.dumps({'stopped':True}),flush=True);break
            if action=='ticket':result={'url':server.issue_ticket()}
            elif action=='state':
                value=message['value']
                assert isinstance(value,dict) and set(value)<=set(state) and all(type(v) is bool for v in value.values())
                state.update(value);release.clear();result={'fixtureState':dict(state)}
            elif action=='release':release.set()
            elif action=='engine_hold':
                assert engine_hold is None
                engine_hold=service.store.execution_lock();engine_hold.__enter__();result={'engineHeld':True}
            elif action=='engine_release':
                if engine_hold is not None:engine_hold.__exit__(None,None,None);engine_hold=None
                result={'engineHeld':False}
            elif action=='restart':
                assert not service._ai_studio.get_ai_connection()['busy'], 'Release/cancel active generation before restart'
                if engine_hold is not None:engine_hold.__exit__(None,None,None);engine_hold=None
                server.shutdown();server.server_close();thread.join(5)
                service,server,thread=start();result={'url':server.issue_ticket()}
            elif action=='counters':result={**stats,'fixtureState':dict(state)}
            elif action=='draft':result=service._ai_studio.get_ai_draft(requestId=message['requestId'])
            elif action=='request':result=service.get_ai_request(message['requestId'])
            elif action=='run':result=service.get_run(message['runId'])
            elif action=='mcp_replay':result=asyncio.run(mcp_replay(message['runId']))
            elif action=='inspect':
                secrets=[value.encode() for value in SECRETS]
                data=service.list_data()['datasets']
                assert all(secret not in json.dumps(data) for secret in SECRETS)
                result={**stats,'strategies':service.list_strategies()['strategies'],'runs':service.list_runs()['runs'],'connection':service._ai_studio.get_ai_connection(),'datasets':data,'requestRows':service.list_ai_requests()['requests'],'runHashes':{r['id']:digest(service.get_run(r['id'])) for r in service.list_runs()['runs']},'noSecretsInFiles':no_secrets_in_files(root, secrets)}
                if message.get('requestId'):result['request']=service.get_ai_request(message['requestId'])
                if message.get('draftId'):result['draft']=service._ai_studio.get_ai_draft(requestId=message['draftId'])
                if message.get('runId'):result['run']=service.get_run(message['runId'])
            else:raise ValueError('Unknown fixture command')
            print(json.dumps(result,ensure_ascii=False),flush=True)
    finally:
        release.set()
        if engine_hold is not None:engine_hold.__exit__(None,None,None)
        server.shutdown();server.server_close();thread.join(5)
