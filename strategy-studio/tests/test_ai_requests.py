import asyncio
import copy
import json
import threading
import pytest
from fastmcp import Client, FastMCP
from runtime.service import StrategyRuntime
from runtime.mcp import register_platform
from runtime.storage import canonical, encode_body


def definition():
    return {'schemaVersion':1,'kind':'dsl','name':'baseline','entry':{'op':'gt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},'exit':{'op':'lt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},'quantity':1,'allowPyramiding':False,'risk':{'stopLossPct':None,'takeProfitPct':None}}


@pytest.fixture
def setup(tmp_path):
    service=StrategyRuntime(tmp_path)
    revision=service.save_strategy(definition())
    data=service.import_data([{'date':f'2025-01-0{i+1}','open':p,'high':p,'low':p,'close':p} for i,p in enumerate([10,20,30,40])],{'kind':'imported','label':'test-only'})
    baseline=service.run(revision['id'],data['id'])
    return service,baseline


def request(service, baseline, **kwargs):
    return service.create_ai_request(baseline['id'],'낙폭과 비용을 줄이는 후보',**kwargs)['request']['id']


def test_real_mcp_submission_and_retry_preserve_baseline(setup):
    service,baseline=setup
    async def check():
        mcp=FastMCP('ai-request-test');register_platform(mcp,None,None,service)
        async with Client(mcp) as client:
            created=(await client.call_tool('meritz_ai_request_create',{'runId':baseline['id'],'purpose':'후보 작성','requestId':'a'*32})).structured_content
            ident=created['request']['id']
            assert created['status']=='awaiting_candidates'
            read=(await client.call_tool('meritz_ai_request_get',{'requestId':ident})).structured_content
            assert read==created
            listed=(await client.call_tool('meritz_ai_request_get',{})).structured_content
            assert listed['requests'][0]['request']['id']==ident
            args={'requestId':ident,'candidates':[definition(),{'kind':'invalid'}]}
            first=(await client.call_tool('meritz_ai_candidates_submit',args)).structured_content
            again=(await client.call_tool('meritz_ai_candidates_submit',args)).structured_content
            assert first==again
            return first
    result=asyncio.run(check())
    assert result['status']=='succeeded' and result['job']['outcome']=='completed_with_failures'
    assert [a['status'] for a in result['attempts']]==['succeeded','failed']
    candidate=service.get_run(result['attempts'][0]['runId'])
    assert candidate['parentRunId']==baseline['id']
    assert candidate['manifest']['dataHash']==baseline['manifest']['dataHash']
    assert 'validationJobId' not in candidate and candidate['aiRequestId']==result['request']['id']
    assert service.get_run(baseline['id'])==baseline
    assert len(service.list_runs()['runs'])==2 and service.list_runs()['validationJobs']==[]
    assert result['result']['comparison']
    assert [checkpoint['status'] for checkpoint in result['prepared']] == ['prepared']
    assert result['prepared'][0]['revisionId'] == candidate['revisionId']
    assert service.list_ai_requests()['requests'][0]['outcome'] == 'completed_with_failures'


def test_all_invalid_candidates_are_marked_in_request_summary(setup):
    service, baseline = setup
    ident = request(service, baseline)
    service.submit_ai_candidates(ident, [{'kind':'invalid'}])
    row = service.list_ai_requests()['requests'][0]
    assert row['status'] == 'succeeded' and row['outcome'] == 'completed_with_failures'
    assert len(service.list_runs()['runs']) == 1


def test_submission_payload_tampering_is_rejected_on_read_and_retry(setup):
    service, baseline = setup
    ident = request(service, baseline)
    original = [definition()]
    service.submit_ai_candidates(ident, original)
    value = service.store.get('ai-submission', ident)
    value['candidates'][0]['name'] = 'altered'
    with service.store.connection() as con:
        con.execute('UPDATE objects SET body=? WHERE kind=? AND id=?',
                    (encode_body(value), 'ai-submission', ident))
    before = service.list_runs()
    with pytest.raises(ValueError, match='무결성'):
        service.get_ai_request(ident)
    with pytest.raises(ValueError, match='무결성'):
        service.submit_ai_candidates(ident, original)
    assert service.list_runs() == before


def test_legacy_prepared_checkpoint_is_not_reported_as_failed_execution(setup):
    service, baseline = setup
    ident = request(service, baseline)
    legacy = {'index':0, 'revisionId':baseline['revisionId'], 'stage':'revision', 'status':'failed'}
    service.store.put('ai-prepared', ident+'-0', legacy)
    before = service.store.get('ai-prepared', ident+'-0')
    response = service.get_ai_request(ident)
    assert response['prepared'] == [{**legacy, 'status':'prepared'}]
    assert response['attempts'] == [] and response['status'] == 'awaiting_candidates'
    assert service.store.get('ai-prepared', ident+'-0') == before
    assert len(service.list_runs()['runs']) == 1


def test_create_and_submit_idempotency_boundaries(setup):
    service,base=setup;ident=request(service,base,requestId='b'*32)
    assert request(service,base,requestId='b'*32)==ident
    with pytest.raises(ValueError): service.create_ai_request(base['id'],'different',ident)
    for candidates in ([],[{}]*4,['bad']):
        with pytest.raises(ValueError): service.submit_ai_candidates(ident,candidates)
    assert service.get_ai_request(ident)['status']=='awaiting_candidates'
    service.submit_ai_candidates(ident,[definition()])
    with pytest.raises(ValueError): service.submit_ai_candidates(ident,[{'bad':True}])
    assert len(service.list_runs()['runs'])==2


@pytest.mark.parametrize('kind',['revision','dataset','run'])
def test_source_tampering_is_recorded_without_execution(setup,kind):
    service,base=setup;ident=request(service,base)
    key={'revision':base['revisionId'],'dataset':base['datasetId'],'run':base['id']}[kind]
    value=service.store.get(kind,key)
    if kind=='revision': value['definition']['name']='changed'
    elif kind=='dataset': value['bars'][0]['close']=999
    else: value['settings']['feeBps']=999
    with service.store.connection() as con: con.execute('UPDATE objects SET body=? WHERE kind=? AND id=?',(canonical(value),kind,key))
    result=service.submit_ai_candidates(ident,[definition()])
    assert result['status']=='failed' and result['job']['stage']=='source'
    assert result['runs']==[]


def test_execution_failure_sanitized_and_success_not_discarded(setup,monkeypatch):
    service,base=setup;ident=request(service,base);original=service._calculate
    bad=definition();bad['name']='fails'
    def calculate(definition,*args,**kwargs):
        if definition['name']=='fails': raise ValueError('SECRET diagnostic should not escape')
        return original(definition,*args,**kwargs)
    monkeypatch.setattr(service,'_calculate',calculate)
    result=service.submit_ai_candidates(ident,[bad,definition()])
    assert [a['status'] for a in result['attempts']]==['failed','succeeded']
    assert result['attempts'][0]['runId'] and 'SECRET' not in json.dumps(result)
    assert result['result']['comparison']


def test_concurrent_retry_and_cancel(setup,monkeypatch):
    service,base=setup;ident=request(service,base);entered=threading.Event();release=threading.Event();output=[]
    def slow(definition,data,settings,cancel=None):
        entered.set();assert release.wait(5);assert cancel.is_set();raise ValueError('cancelled')
    monkeypatch.setattr(service,'_calculate',slow)
    thread=threading.Thread(target=lambda:output.append(service.submit_ai_candidates(ident,[definition(),definition()])))
    thread.start()
    try:
        assert entered.wait(3)
        assert service.submit_ai_candidates(ident,[definition(),definition()])['status']=='running'
        other=StrategyRuntime(service.store.root)
        assert other.submit_ai_candidates(ident,[definition(),definition()])['status']=='running'
        service.cancel(jobId='ai-'+ident)
    finally: release.set();thread.join(5)
    assert not thread.is_alive()
    result=output[0];assert result['status']=='cancelled'
    assert len(result['runs'])==1 and result['runs'][0]['status']=='cancelled'
    assert result['result']['unattemptedCount']==1 and service._active=={}


@pytest.mark.parametrize('failure',['comparison','persistence','interrupt'])
def test_stage_failure_recovery_never_reexecutes(setup,monkeypatch,failure):
    service,base=setup;ident=request(service,base)
    if failure in ('comparison','interrupt'):
        def broken(*args):
            if failure=='interrupt': raise SystemExit('abrupt')
            raise ValueError('SECRET')
        monkeypatch.setattr(service,'compare',broken)
    else:
        original=service.store.put
        def put(kind,*args):
            if kind=='ai-attempt': raise OSError('SECRET')
            return original(kind,*args)
        monkeypatch.setattr(service.store,'put',put)
    if failure=='interrupt':
        with pytest.raises(SystemExit): service.submit_ai_candidates(ident,[definition()])
    else: service.submit_ai_candidates(ident,[definition()])
    restarted=StrategyRuntime(service.store.root)
    result=restarted.submit_ai_candidates(ident,[definition()])
    assert result['status']==('interrupted' if failure=='interrupt' else 'failed')
    assert len(result['runs'])==1 and len(restarted.list_runs()['runs'])==2
    assert result['prepared'] and 'SECRET' not in json.dumps(result)
    assert service._active=={}


def test_abandoned_job_recovered_with_candidate_provenance(setup):
    service,base=setup;ident=request(service,base);job='ai-'+ident
    from runtime.storage import digest
    service.store.put('ai-request-job',job,{'id':job,'runId':base['id'],'status':'running','submissionHash':digest([definition()])})
    service.store.put('run','c'*32,{'id':'c'*32,'status':'running','aiJobId':job,'aiRequestId':ident,'parentRunId':base['id']})
    restarted=StrategyRuntime(service.store.root)
    result=restarted.submit_ai_candidates(ident,[definition()])
    assert result['status']=='interrupted' and result['runs'][0]['status']=='interrupted'
    assert len(restarted.list_runs()['runs'])==2


def test_cancel_before_submission_and_terminal_retry(setup):
    service,base=setup;ident=request(service,base)
    assert service.cancel_ai_request(ident)['status']=='cancelled'
    assert service.submit_ai_candidates(ident,[definition()])['status']=='cancelled'
    assert len(service.list_runs()['runs'])==1


def test_real_process_kill_recovers_without_reexecution(setup):
    import os
    import subprocess
    import sys
    from pathlib import Path
    service,base=setup;ident=request(service,base)
    script='''import sys,json,time
from runtime.service import StrategyRuntime
service=StrategyRuntime(sys.argv[1])
def pause(*args,**kwargs):
 print('started',flush=True)
 time.sleep(30)
service._calculate=pause
service.submit_ai_candidates(sys.argv[2],json.loads(sys.argv[3]))
'''
    child=subprocess.Popen([sys.executable,'-c',script,str(service.store.root),ident,json.dumps([definition()])],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]),'MERITZ_NO_ENV_FILE':'1'})
    try:
        # communicate is bounded; kill only after the durable engine attempt exists.
        import selectors
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout,selectors.EVENT_READ)
            assert selector.select(5)
            assert child.stdout.readline().strip()=='started'
    finally:
        child.kill();child.communicate(timeout=5)
    restarted=StrategyRuntime(service.store.root)
    result=restarted.submit_ai_candidates(ident,[definition()])
    assert result['status']=='interrupted' and result['runs'][0]['status']=='interrupted'
    assert result['prepared'] and result['result'] is None
    assert len(restarted.list_runs()['runs'])==2


def test_caught_interrupt_marks_child_before_restart(setup,monkeypatch):
    service,base=setup;ident=request(service,base)
    def interrupted(*args,**kwargs): raise KeyboardInterrupt()
    monkeypatch.setattr(service,'_calculate',interrupted)
    with pytest.raises(KeyboardInterrupt): service.submit_ai_candidates(ident,[definition()])
    state=service.get_ai_request(ident)
    assert state['status']=='interrupted' and state['runs'][0]['status']=='interrupted'
    assert state['submission']['origin']=='external-host-submitted'
    assert state['submission']['modelInvocationVerified'] is False


@pytest.mark.parametrize('ident',['',0,False,[],{},1,True,'A'*32,'a'*31])
def test_explicit_invalid_request_id_is_not_generated(setup,ident):
    service,base=setup
    with pytest.raises(ValueError,match='requestId'):
        service.create_ai_request(base['id'],'개선',ident)
    assert service.list_ai_requests()['requests']==[]


def test_mcp_empty_read_id_is_error_not_listing(setup):
    service,base=setup;request(service,base)
    async def check():
        mcp=FastMCP('empty-id-test');register_platform(mcp,None,None,service)
        async with Client(mcp) as client:
            with pytest.raises(Exception,match='Invalid snapshot identifier'):
                await client.call_tool('meritz_ai_request_get',{'requestId':''})
            listed=(await client.call_tool('meritz_ai_request_get',{'requestId':None})).structured_content
            assert len(listed['requests'])==1
    asyncio.run(check())
