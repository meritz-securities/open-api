"""HTTP improvement: real session/transport/parser/SQLite/engine, generated wire only."""
import asyncio
import copy
import json
import os
import socket
import threading
import time

import httpx
import pytest

from runtime import ai_generation, ai_requests
from runtime.ai_http_provider import generate_candidates
from runtime.ai_session import AISession
from runtime.ai_http_provider import HttpProviderConfig
from runtime.service import StrategyRuntime
from runtime.storage import digest
from test_ai_studio_http import definition, Body
from test_ai_generation import fake

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='coordinator locks remain POSIX-only')


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:pytest.fail('network prohibited'))
    runtime = StrategyRuntime(tmp_path/'store',allow_ai_connection=True)
    revision=runtime.save_strategy(definition())
    data=runtime.import_data([{'date':f'2025-01-0{i+1}','open':p,'high':p,'low':p,'close':p} for i,p in enumerate([10,20,30,40])],{'kind':'imported','label':'PRIVATE_SOURCE_LABEL'})
    baseline=runtime.run(revision['id'],data['id'],{'initialCash':100,'feeBps':0,'slippageBps':0,'taxBps':0})
    ident=runtime.create_ai_request(baseline['id'],'Keep one unit, explain no magic profits')['request']['id']
    state={'calls':[], 'hold':False, 'entered':threading.Event(), 'release':threading.Event()}
    async def wire(request):
        assert request.headers['x-api-key']=='GENERATED_HTTP_SECRET'
        body=json.loads(request.content)
        assert body['model']=='fixture-model' and body['max_tokens']==1024
        assert 'tools' not in body
        assert 'GENERATED_HTTP_SECRET' not in request.content.decode()
        assert 'PRIVATE_SOURCE_LABEL' not in request.content.decode()
        state['calls'].append(body);state['entered'].set()
        while state['hold'] and not state['release'].is_set():await asyncio.sleep(.01)
        return httpx.Response(200,headers={'content-type':'application/json'},stream=Body())
    def send(config,prompt,event):
        return generate_candidates(config,prompt,event,_transport_factory=lambda:httpx.MockTransport(wire))
    runtime._ai_studio.session=AISession(config_factory=HttpProviderConfig,send=send,models=['fixture-model'],management_allowed=True)
    config=runtime._ai_studio.connect_ai_session(apiKey='GENERATED_HTTP_SECRET',model='fixture-model',maxOutputTokens=1024,timeoutSeconds=5)['configuration']
    yield runtime,baseline,ident,config,state
    state['release'].set()


def preview(system):
    runtime,_,ident,config,_=system
    return runtime._ai_studio.preview_ai_http_improvement(requestId=ident,connectionId=config['connectionId'])


def invoke(runtime,ident,p):
    return runtime._ai_studio.generate_ai_http_improvement(requestId=ident,promptHash=p['promptHash'],consentVersion=p['consentVersion'])


def test_real_http_engine_and_no_second_model(system,tmp_path):
    runtime,baseline,ident,config,state=system
    p=preview(system)
    assert state['calls']==[] and 'maxBudgetUsd' not in p
    assert runtime.get_ai_request(ident)['generationPreview']['provider']=='claude-api'
    result=invoke(runtime,ident,p)
    assert result['status']=='succeeded',result
    assert len(state['calls'])==1
    assert result['generation']['provider']=='claude-api'
    assert result['generation']['providerAttempted'] is True
    assert result['submission']['origin']=='local-http-generated'
    assert result['submission']['modelInvocationVerified'] is False
    child=runtime.get_run(result['attempts'][0]['runId'])
    assert child['report']['metrics']['finalEquity']==120
    assert child['report']['trades'][0]['date']=='2025-01-02'
    assert child['manifest']['dataHash']==baseline['manifest']['dataHash']
    assert child['settings']==baseline['settings'] and result['result']['comparison']
    assert runtime.get_run(baseline['id'])==baseline
    runtime._ai_studio.disconnect_ai_session()
    assert invoke(runtime,ident,p)==result and len(state['calls'])==1
    for path in tmp_path.rglob('*'):
        if path.is_file():assert b'GENERATED_HTTP_SECRET' not in path.read_bytes()


def test_ready_reconnect_without_key_sender_or_new_consent(system):
    runtime,_,ident,_,state=system;p=preview(system)
    with runtime.store.execution_lock():assert invoke(runtime,ident,p)['status']=='ready'
    runtime._ai_studio.disconnect_ai_session()
    other=StrategyRuntime(runtime.store.root)
    assert other._ai_studio.preview_ai_http_improvement(requestId=ident)==p
    assert other.list_ai_requests()['requests'][0]['generation']['connectionId']==p['connectionId']
    with pytest.raises(ValueError,match='CONSENT_MISMATCH'):
        invoke(other,ident,{**p,'promptHash':'bad'})
    assert invoke(other,ident,p)['status']=='succeeded'
    assert len(state['calls'])==1


def test_connection_rotation_and_explicit_consent(system):
    runtime,_,ident,_,state=system;p=preview(system)
    for change in ({'promptHash':'x'},{'consentVersion':'x'}):
        with pytest.raises(ValueError,match='CONSENT_MISMATCH'):invoke(runtime,ident,{**p,**change})
    runtime._ai_studio.connect_ai_session(apiKey='GENERATED_OTHER_KEY',model='fixture-model')
    assert runtime._ai_studio.preview_ai_http_improvement(requestId=ident)==p
    with pytest.raises(ValueError,match='AI_CONNECTION_CHANGED'):invoke(runtime,ident,p)
    assert not state['calls'] and runtime.get_ai_request(ident)['generation'] is None


def test_cli_http_both_direction_conflict(system,tmp_path):
    runtime,baseline,ident,_,state=system;p=preview(system)
    runtime._ai_provider,marker=fake(tmp_path)
    with pytest.raises(ValueError,match='PROVIDER_CONFLICT'):runtime.preview_ai_generation(ident)
    with pytest.raises(ValueError,match='PROVIDER_CONFLICT'):runtime.generate_ai_candidates(ident,p['promptHash'],p['consentVersion'])
    other=runtime.create_ai_request(baseline['id'],'CLI owned')['request']['id']
    cli=runtime.preview_ai_generation(other)
    with runtime.store.execution_lock():
        assert runtime.generate_ai_candidates(other,cli['promptHash'],cli['consentVersion'])['status']=='ready'
    with pytest.raises(ValueError,match='PROVIDER_CONFLICT'):
        runtime._ai_studio.preview_ai_http_improvement(requestId=other)
    with pytest.raises(ValueError,match='PROVIDER_CONFLICT'):
        runtime._ai_studio.generate_ai_http_improvement(requestId=other,promptHash=cli['promptHash'],consentVersion=cli['consentVersion'])
    assert not state['calls']


def test_http_external_conflict_and_precancel(system):
    runtime,_,ident,_,state=system;p=preview(system)
    with pytest.raises(ValueError,match='로컬 생성'):runtime.submit_ai_candidates(ident,[definition()])
    runtime.cancel_ai_request(ident)
    runtime._ai_studio.disconnect_ai_session()
    assert invoke(runtime,ident,p)['status']=='cancelled' and not state['calls']


def test_concurrent_http_claim_and_persistent_cancel(system):
    runtime,_,ident,_,state=system;p=preview(system);state['hold']=True
    results=[]
    worker=threading.Thread(target=lambda:results.append(invoke(runtime,ident,p)));worker.start()
    try:
        assert state['entered'].wait(3)
        other=StrategyRuntime(runtime.store.root)
        with pytest.raises(ValueError,match='Another AI generation'):invoke(other,ident,p)
        with pytest.raises(ValueError,match='AI_CONNECTION_BUSY'):runtime._ai_studio.disconnect_ai_session()
        other.cancel_ai_request(ident)
        worker.join(5);assert not worker.is_alive()
        assert results[0]['status']=='cancelled'
        assert len(state['calls'])==1 and len(runtime.list_runs()['runs'])==1
        assert invoke(other,ident,p)['status']=='cancelled'
    finally:
        state['release'].set();worker.join(5)


@pytest.mark.parametrize('late',['user','control'])
def test_late_error_keeps_completed_result(system,monkeypatch,late):
    runtime,_,ident,_,state=system;p=preview(system);put=runtime.store.put
    def intercept(kind,key,value):
        output=put(kind,key,value)
        if kind=='ai-result':
            if late=='user':runtime.cancel_ai_request(ident)
            else:
                event=runtime._active['ai-'+ident];event.control_error='CANCEL_STATE_UNAVAILABLE';event.set()
        return output
    monkeypatch.setattr(runtime.store,'put',intercept)
    result=invoke(runtime,ident,p)
    assert result['status']=='succeeded' and result['result']['comparison']
    assert len(state['calls'])==1


def test_ready_tamper_validation_without_model(system):
    runtime,_,ident,_,state=system;p=preview(system)
    with runtime.store.execution_lock():assert invoke(runtime,ident,p)['status']=='ready'
    from runtime.storage import encode_body
    record=runtime.store.get('ai-generation',ident)
    record['candidates'][0]['kind']='python';record['candidateHash']=digest(record['candidates'])
    with runtime.store.connection() as con:
        con.execute("UPDATE objects SET body=? WHERE kind='ai-generation' AND id=?",(encode_body(record),ident))
    runtime._ai_studio.disconnect_ai_session()
    with pytest.raises(ValueError):invoke(runtime,ident,p)
    assert len(state['calls'])==1 and len(runtime.list_runs()['runs'])==1


@pytest.mark.parametrize('stage',['claim','attempt','ready','result'])
def test_persistence_failure_no_duplicate_or_secret(system,monkeypatch,stage):
    runtime,_,ident,_,state=system;p=preview(system)
    put=runtime.store.put;transition=runtime.store.transition_ai_generation
    def fail_put(kind,key,value):
        match={'claim':'ai-generation','attempt':'ai-generation-attempt','result':'ai-result'}.get(stage)
        if kind==match:raise ValueError('GENERATED_PRIVATE_STORAGE_MESSAGE')
        return put(kind,key,value)
    def fail_transition(key,value):
        if stage=='ready' and value['status']=='ready':raise ValueError('GENERATED_PRIVATE_STORAGE_MESSAGE')
        return transition(key,value)
    monkeypatch.setattr(runtime.store,'put',fail_put);monkeypatch.setattr(runtime.store,'transition_ai_generation',fail_transition)
    try:
        result=invoke(runtime,ident,p)
        assert 'GENERATED_PRIVATE_STORAGE_MESSAGE' not in json.dumps(result)
    except ValueError as error:
        assert 'GENERATED_PRIVATE_STORAGE_MESSAGE' not in str(error) and error.__context__ is None
    count=len(state['calls'])
    monkeypatch.setattr(runtime.store,'put',put);monkeypatch.setattr(runtime.store,'transition_ai_generation',transition)
    if stage!='claim':
        assert invoke(runtime,ident,p)['status'] in ('failed','interrupted')
        assert len(state['calls'])==count
    if stage in ('claim','attempt'):assert count==0


@pytest.mark.parametrize('output',[[],[{'kind':'python'}],None])
def test_provider_output_validated_independently(system,output):
    runtime,_,ident,_,state=system;p=preview(system)
    runtime._ai_studio.session._send=lambda *a:output
    result=invoke(runtime,ident,p)
    assert result['status']=='failed' and result['generation']['error']=='PROVIDER_INVALID_OUTPUT'
    assert len(runtime.list_runs()['runs'])==1
    assert invoke(runtime,ident,p)==result


def test_provider_exception_never_persists_raw_error(system):
    from runtime.ai_cli_provider import ProviderError
    runtime,_,ident,_,_=system;p=preview(system)
    def error(*args):raise ProviderError('GENERATED_PRIVATE_PROVIDER_MESSAGE')
    runtime._ai_studio.session._send=error
    result=invoke(runtime,ident,p)
    assert result['generation']['error']=='PROVIDER_FAILED'
    assert 'GENERATED_PRIVATE_PROVIDER_MESSAGE' not in json.dumps(result)


def test_watcher_read_error_is_control_failure(system,monkeypatch):
    runtime,_,ident,_,state=system;p=preview(system);state['hold']=True
    original=ai_generation.cancelled;owner=threading.current_thread()
    def fail(service,request):
        if threading.current_thread() is not owner and state['entered'].is_set():
            raise ValueError('PRIVATE_WATCHER')
        return original(service,request)
    monkeypatch.setattr(ai_generation,'cancelled',fail)
    result=invoke(runtime,ident,p)
    assert result['status']=='failed' and result['generation']['error']=='CANCEL_STATE_UNAVAILABLE'
    assert result['cancellationRequested'] is False
    assert len(state['calls'])==1 and len(runtime.list_runs()['runs'])==1


def test_cancel_after_ready_before_submit_does_not_save_revision(system,monkeypatch):
    runtime,_,ident,_,state=system;p=preview(system);submit=ai_requests.submit
    def intercept(service,request,candidates,**kwargs):
        service.cancel_ai_request(request)
        return submit(service,request,candidates,**kwargs)
    monkeypatch.setattr(ai_requests,'submit',intercept)
    result=invoke(runtime,ident,p)
    assert result['status']=='cancelled' and result['job'] is None
    assert len(runtime.list_strategies()['strategies'])==1 and len(state['calls'])==1


def test_precancel_marker_prevents_sender_attempt(system,monkeypatch):
    from contextlib import contextmanager
    runtime,_,ident,_,state=system;p=preview(system)
    @contextmanager
    def cancel_at_start(service,request,event):
        service.cancel_ai_request(request)
        yield
    monkeypatch.setattr(ai_generation,'_watch_cancel',cancel_at_start)
    result=invoke(runtime,ident,p)
    assert result['status']=='cancelled' and result['generation']['providerAttempted'] is False
    assert not state['calls']


def test_windows_coordinator_fails_closed(system,monkeypatch):
    runtime,_,ident,config,state=system
    # Patch the platform guard itself, not process-global os.name/Path semantics.
    def unsupported():raise ValueError('PROVIDER_PLATFORM_UNSUPPORTED')
    monkeypatch.setattr(ai_generation,'_platform',unsupported)
    with pytest.raises(ValueError,match='PROVIDER_PLATFORM_UNSUPPORTED'):preview(system)
    assert not state['calls'] and runtime.store.list('ai-generation-preview')==[]


@pytest.mark.parametrize('field,value',[('maxBudgetUsd',1),('apiKey','private'),('provider','other'),('connectionId','bad')])
def test_trusted_metadata_rejects_unknown_or_invalid(system,field,value):
    from runtime.ai_generation_binding import GenerationBinding
    _,_,_,config,_=system
    with pytest.raises(ValueError,match='METADATA_INVALID'):
        GenerationBinding({**config,field:value},lambda *a:[])


def test_source_tamper_rejected_before_model(system):
    from runtime.storage import encode_body
    runtime,baseline,ident,_,state=system;p=preview(system)
    source=runtime.store.get('run',baseline['id']);source['settings']['feeBps']=100
    with runtime.store.connection() as con:
        con.execute("UPDATE objects SET body=? WHERE kind='run' AND id=?",(encode_body(source),baseline['id']))
    with pytest.raises(ValueError,match='AI_GENERATION_FAILED'):invoke(runtime,ident,p)
    assert not state['calls'] and runtime.get_ai_request(ident)['generation'] is None


def _process_http_generation(root, ident, approved, marker, result_path):
    """A second real process owns generation; transport remains generated/fake."""
    from pathlib import Path
    from runtime.ai_generation_binding import GenerationBinding
    async def wire(request):
        Path(marker).write_text('one request')
        await asyncio.sleep(20)
        return httpx.Response(200,headers={'content-type':'application/json'},stream=Body())
    config=HttpProviderConfig(api_key='GENERATED_HTTP_SECRET',model='fixture-model',max_tokens=1024,timeout_seconds=5)
    binding=GenerationBinding({k:approved[k] for k in ('provider','model','maxOutputTokens','timeoutSeconds','connectionId')},
                              lambda prompt,event:generate_candidates(config,prompt,event,_transport_factory=lambda:httpx.MockTransport(wire)))
    service=StrategyRuntime(root)
    result=ai_generation.generate_http(service,ident,approved['promptHash'],approved['consentVersion'],binding)
    Path(result_path).write_text(result['status'])


def test_real_process_http_competition_and_cancel(system,tmp_path):
    import multiprocessing
    runtime,_,ident,_,_=system;p=preview(system)
    marker=tmp_path/'wire-start';result=tmp_path/'result'
    process=multiprocessing.get_context('spawn').Process(target=_process_http_generation,args=(runtime.store.root,ident,p,str(marker),str(result)))
    process.start()
    try:
        until=time.monotonic()+5
        while not marker.exists() and time.monotonic()<until:time.sleep(.01)
        assert marker.exists()
        other=StrategyRuntime(runtime.store.root)
        with pytest.raises(ValueError,match='Another AI generation'):invoke(other,ident,p)
        other.cancel_ai_request(ident)
        process.join(7);assert process.exitcode==0
        assert result.read_text()=='cancelled'
        assert invoke(other,ident,p)['status']=='cancelled'
        assert len(other.list_runs()['runs'])==1
    finally:
        if process.is_alive():process.kill();process.join(5)


@pytest.mark.parametrize('state',['ready','failed','cancelled','interrupted'])
def test_legacy_cli_generation_rows_remain_usable(system,tmp_path,state):
    runtime,_,ident,_,calls=system
    runtime._ai_provider,marker=fake(tmp_path)
    p=runtime.preview_ai_generation(ident)
    old={'id':ident,'status':state,'promptHash':p['promptHash'],'consentVersion':p['consentVersion'],
         'model':p['model'],'maxBudgetUsd':p['maxBudgetUsd'],'providerAttempted':True,'modelInvocationVerified':False}
    if state=='ready':old.update(candidates=[definition()],candidateHash=digest([definition()]),validatedOutputReceived=True)
    runtime.store.put('ai-generation',ident,old)
    result=runtime.generate_ai_candidates(ident,p['promptHash'],p['consentVersion'])
    assert result['status']==('succeeded' if state=='ready' else state)
    assert result['generation']['maxBudgetUsd']==p['maxBudgetUsd']
    assert not marker.exists() and not calls['calls']
    if state=='ready':assert result['submission']['origin']=='local-cli-generated'


@pytest.mark.parametrize('field,value',[('consentVersion','changed'),('connectionId','0'*32),('maxOutputTokens',1),('preview',{})])
def test_ready_metadata_tamper_cannot_submit(system,field,value):
    from runtime.storage import encode_body
    runtime,_,ident,_,state=system;p=preview(system)
    with runtime.store.execution_lock():assert invoke(runtime,ident,p)['status']=='ready'
    record=runtime.store.get('ai-generation',ident);record[field]=value
    with runtime.store.connection() as con:
        con.execute("UPDATE objects SET body=? WHERE kind='ai-generation' AND id=?",(encode_body(record),ident))
    with pytest.raises(ValueError,match='AI_GENERATION_INTEGRITY_ERROR'):invoke(runtime,ident,p)
    assert len(state['calls'])==1 and len(runtime.list_runs()['runs'])==1
