"""Real Studio HTTP/session/Store/engine. Only model wire + Meritz call are fake.
No real AI invocation, credentials, profile, live prices, orders or external network.
"""
import asyncio
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


KEY='GENERATED_NEW_STRATEGY_TEST_KEY'
stats={'modelWireRequests':0,'marketFixtureCalls':0,'outboundAttempts':0}
state={'hold':False,'fail':False,'loseCreate':False,'loseGenerate':False}
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
    async def __aiter__(self):
        yield json.dumps({'type':'message','role':'assistant','stop_reason':'end_turn','content':[{'type':'text','text':json.dumps({'candidates':[DEFINITION]})}]}).encode()
async def model_handler(request):
    assert str(request.url)=='https://api.anthropic.com/v1/messages'
    assert request.headers['x-api-key']==KEY
    stats['modelWireRequests']+=1
    while state['hold'] and not release.is_set():await asyncio.sleep(.01)
    if state['fail']:return httpx.Response(401,json={'error':{'message':'generated failure'}})
    return httpx.Response(200,headers={'content-type':'application/json'},stream=Body())
def send(config,prompt,cancel):
    return generate_candidates(config,prompt,cancel,_transport_factory=lambda:httpx.MockTransport(model_handler))
ai_studio.generate_candidates=send

with tempfile.TemporaryDirectory(prefix='ai-new-runtime-') as temp:
    root=Path(temp)
    def start():
        service=StrategyRuntime(root,catalog=load_catalog(),allow_market_connection=True,allow_ai_connection=True,ai_studio=True)
        original_create=service._ai_studio.create_ai_draft;original_generate=service._ai_studio.generate_ai_draft
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
        service._ai_studio.create_ai_draft=create;service._ai_studio.generate_ai_draft=generate
        server=create_server(service,page=Path(sys.argv[1]).read_bytes())
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        return service,server,thread
    service,server,thread=start()
    print(json.dumps({'url':server.issue_ticket(),'fixture':'fake model wire + fake Meritz historical prices'}),flush=True)
    try:
        for line in sys.stdin:
            message=json.loads(line);action=message['action'];result={}
            if action=='stop':break
            if action=='ticket':result={'url':server.issue_ticket()}
            elif action=='state':
                state.update(message['value']);release.clear()
            elif action=='release':release.set()
            elif action=='restart':
                server.shutdown();server.server_close();thread.join(5)
                service,server,thread=start();result={'url':server.issue_ticket()}
            elif action=='counters':result={**stats,'fixtureState':dict(state)}
            elif action=='draft':result=service._ai_studio.get_ai_draft(requestId=message['requestId'])
            elif action=='inspect':
                secrets=[KEY.encode(),b'generated-market-key',b'generated-market-secret']
                result={**stats,'strategies':service.list_strategies()['strategies'],'runs':service.list_runs()['runs'],'connection':service._ai_studio.get_ai_connection(),'noSecretsInFiles':no_secrets_in_files(root, secrets)}
                if message.get('requestId'):result['draft']=service._ai_studio.get_ai_draft(requestId=message['requestId'])
            else:raise ValueError('Unknown fixture command')
            print(json.dumps(result,ensure_ascii=False),flush=True)
    finally:
        release.set();server.shutdown();server.server_close();thread.join(5)
