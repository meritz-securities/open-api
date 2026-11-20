"""Independent initial-draft oracle. No actual CLI/provider/authentication calls."""
import copy
import json
import multiprocessing
import os
import threading
import time

import pytest

from runtime.ai_drafts import DraftController, DraftError
from runtime.dsl import templates
from runtime.service import StrategyRuntime
from runtime.storage import Store, canonical, digest


def definition():
    return {'schemaVersion':1,'kind':'dsl','name':'initial draft',
            'entry':{'op':'gt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},
            'exit':{'op':'lt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},
            'quantity':1,'allowPyramiding':False,'risk':{'stopLossPct':None,'takeProfitPct':None}}


def metadata(**changes):
    return {'provider':'test-fixture','model':'fixture-model','maxOutputTokens':1024,
            'timeoutSeconds':2,'connectionId':'connection-1',**changes}


def generate(controller,preview):
    return controller.generate(preview['id'],preview['promptHash'],preview['consentVersion'])


def test_initial_draft_no_fake_baseline_then_explicit_engine_oracle(tmp_path):
    store=Store(tmp_path);calls=[]
    def sender(prompt,event):calls.append(prompt);return [definition()]
    controller=DraftController(store,sender,metadata())
    preview=controller.create('처음부터 단순한 조건 전략을 만들어줘')
    assert preview['state']=='preview' and calls==[]
    assert preview['request'] in preview['userPrompt']
    assert preview['previewScope'].startswith('user-data-only')
    assert store.list('run')==[] and store.list('revision')==[]
    ready=generate(controller,preview)
    assert ready['state']=='ready' and ready['result']['kind']=='draft'
    assert store.list('run')==[] and store.list('revision')==[]
    assert store.list('ai-request')==[]
    assert ready['result']['candidateHash']==digest([definition()])
    assert calls==[preview['userPrompt']]
    assert generate(controller,preview)==ready
    reconnected=DraftController(Store(tmp_path),lambda *a:pytest.fail('must not regenerate'),metadata())
    assert reconnected.get(preview['id'])==ready
    assert generate(reconnected,preview)==ready
    # Only this explicit caller action applies the draft; the controller never does.
    runtime=StrategyRuntime(tmp_path)
    revision=runtime.save_strategy(ready['result']['candidates'][0])
    data=runtime.import_data([{'date':f'2025-01-0{i+1}','open':p,'high':p,'low':p,'close':p}
                              for i,p in enumerate([10,20,30,40])],{'kind':'imported','label':'oracle'})
    run=runtime.run(revision['id'],data['id'],{'initialCash':100,'feeBps':0,'slippageBps':0,'taxBps':0})
    assert run['report']['metrics']['finalEquity']==120
    assert run['report']['trades'][0]['date']=='2025-01-02'
    assert run['manifest']['dataHash']==data['contentHash']


def test_consent_config_binding_and_create_idempotency(tmp_path):
    calls=[];store=Store(tmp_path)
    original_meta=metadata()
    controller=DraftController(store,lambda *a:calls.append(1) or [definition()],original_meta)
    original_meta['model']='mutated-external-dict'
    preview=controller.create('goal',requestId='a'*32)
    assert preview['metadata']['model']=='fixture-model'
    assert controller.create('goal',requestId='a'*32)==preview
    with pytest.raises(DraftError,match='DRAFT_ID_CONFLICT'):controller.create('other',requestId='a'*32)
    for badhash,badversion in [('bad',preview['consentVersion']),(preview['promptHash'],'bad')]:
        with pytest.raises(DraftError,match='DRAFT_CONSENT_MISMATCH'):
            controller.generate(preview['id'],badhash,badversion)
    for changed in (metadata(model='other'),metadata(connectionId='connection-2'),metadata(maxOutputTokens=2048)):
        other=DraftController(store,lambda *a:pytest.fail('no sender call'),changed)
        with pytest.raises(DraftError,match='DRAFT_CONFIG_CHANGED'):generate(other,preview)
    assert calls==[] and controller.get(preview['id'])['state']=='preview'


@pytest.mark.parametrize('bad',[{'api_key':'SECRET'},metadata(secret='SECRET'),metadata(timeoutSeconds=True),
                               metadata(maxOutputTokens=0),metadata(timeoutSeconds=float('nan'))])
def test_unknown_or_invalid_metadata_rejected_without_context(tmp_path,bad):
    with pytest.raises(DraftError) as error:DraftController(Store(tmp_path),lambda *a:[],bad)
    assert str(error.value)=='DRAFT_INVALID_METADATA'
    assert error.value.__context__ is None and 'SECRET' not in str(error.value)


@pytest.mark.parametrize('text',['',' '*5,'x'*2001,'\ud800',None])
def test_bounded_request_and_identifiers(tmp_path,text):
    controller=DraftController(Store(tmp_path),lambda *a:[],metadata())
    with pytest.raises(DraftError,match='DRAFT_INVALID_REQUEST'):controller.create(text)
    for ident in ('../bad','x'*100,'A'*32,'x'):
        with pytest.raises(DraftError,match='DRAFT_INVALID_ID'):controller.get(ident)


def test_concurrent_controllers_claim_once_and_cancel(tmp_path):
    started=threading.Event();calls=[]
    def sender(prompt,event):
        calls.append(1);started.set();assert event.wait(3)
        raise RuntimeError('PRIVATE_PROVIDER_RESPONSE')
    store=Store(tmp_path);first=DraftController(store,sender,metadata())
    second=DraftController(Store(tmp_path),sender,metadata())
    preview=first.create('goal');results=[]
    worker=threading.Thread(target=lambda:results.append(generate(first,preview)))
    worker.start();assert started.wait(2)
    active=generate(second,preview)
    assert active['state']=='generating' and active['owner']['liveness']=='unknown'
    assert second.cancel(preview['id'])['cancelRequested'] is True
    worker.join(4);assert not worker.is_alive()
    assert results[0]['state']=='cancelled'
    assert calls==[1]
    assert generate(first,preview)['state']=='cancelled'
    assert 'PRIVATE_PROVIDER_RESPONSE' not in json.dumps(results)


def test_cancel_preview_never_calls_sender(tmp_path):
    controller=DraftController(Store(tmp_path),lambda *a:pytest.fail('no provider'),metadata())
    preview=controller.create('goal')
    assert controller.cancel(preview['id'])['state']=='cancelled'
    assert generate(controller,preview)['state']=='cancelled'


@pytest.mark.parametrize('output',[[],[definition()]*4,[{'kind':'python','code':'raise Exception()'}],
                                   [{**definition(),'unknown':'bad'}],[{**definition(),'quantity':float('nan')}],
                                   [{**definition(),'name':'x'*100000}]*3])
def test_invalid_output_terminal_no_retry(tmp_path,output):
    calls=[]
    controller=DraftController(Store(tmp_path),lambda *a:calls.append(1) or output,metadata())
    preview=controller.create('goal')
    failed=generate(controller,preview)
    assert failed['state']=='failed' and failed['error']=='DRAFT_INVALID_OUTPUT'
    assert 'result' not in failed
    assert generate(controller,preview)==failed and calls==[1]


def test_timeout_and_control_read_failure_are_distinct(tmp_path,monkeypatch):
    def sender(prompt,event):assert event.wait(2);return [definition()]
    controller=DraftController(Store(tmp_path/'timeout'),sender,metadata(timeoutSeconds=.05))
    result=generate(controller,controller.create('goal'))
    assert result['state']=='failed' and result['error']=='DRAFT_TIMEOUT'
    other=DraftController(Store(tmp_path/'control'),sender,metadata())
    preview=other.create('goal');original=other.get
    def fail_watch(ident):
        if threading.current_thread() is not threading.main_thread():raise RuntimeError('PRIVATE_DB_DETAIL')
        return original(ident)
    monkeypatch.setattr(other,'get',fail_watch)
    result=generate(other,preview)
    assert result['state']=='failed' and result['error']=='DRAFT_CANCEL_STATE_UNAVAILABLE'
    assert result['cancelRequested'] is False
    assert 'PRIVATE_DB_DETAIL' not in json.dumps(result)


def test_completed_result_ignores_late_cancel_and_result_is_revalidated(tmp_path,monkeypatch):
    controller=DraftController(Store(tmp_path),lambda *a:[definition()],metadata())
    preview=controller.create('goal');original=controller._public;fired=[]
    def late_cancel(value):
        if value['state']=='ready' and not fired:
            fired.append(True);controller.cancel(preview['id'])
        return original(value)
    monkeypatch.setattr(controller,'_public',late_cancel)
    result=generate(controller,preview)
    assert result['state']=='ready' and result['cancelRequested'] is False
    assert controller.get(preview['id'])==result
    with controller.store.connection() as con:
        body=json.loads(con.execute('SELECT body FROM ai_drafts WHERE id=?',(preview['id'],)).fetchone()[0])
        body['result']['candidates'][0]['kind']='python'
        body['result']['candidateHash']=digest(body['result']['candidates'])
        con.execute('UPDATE ai_drafts SET body=? WHERE id=?',(canonical(body),preview['id']))
    with pytest.raises(DraftError,match='DRAFT_INTEGRITY_ERROR'):controller.get(preview['id'])


def test_persistence_failure_leaves_uncertain_no_retry_and_no_exception_context(tmp_path,monkeypatch):
    calls=[];controller=DraftController(Store(tmp_path),lambda *a:calls.append(1) or [definition()],metadata())
    preview=controller.create('goal');original=controller._write
    def fail_ready(con,value):
        if value['state']=='ready':raise RuntimeError('SECRET_STORAGE_DETAIL')
        return original(con,value)
    monkeypatch.setattr(controller,'_write',fail_ready)
    with pytest.raises(DraftError,match='DRAFT_STORAGE_ERROR') as error:generate(controller,preview)
    assert error.value.__context__ is None
    assert controller.get(preview['id'])['state']=='generating'
    assert generate(controller,preview)['state']=='generating' and calls==[1]


def _process_generate(root,preview,started,release,queue):
    def sender(prompt,event):
        started.set();release.wait(10);return [definition()]
    controller=DraftController(Store(root),sender,metadata(timeoutSeconds=15))
    queue.put(generate(controller,preview)['state'])


def test_real_process_single_claim_and_killed_owner_stays_uncertain(tmp_path):
    ctx=multiprocessing.get_context('spawn')
    started,release,queue=ctx.Event(),ctx.Event(),ctx.Queue()
    controller=DraftController(Store(tmp_path),lambda *a:pytest.fail('no repeat'),metadata(timeoutSeconds=15))
    preview=controller.create('goal')
    process=ctx.Process(target=_process_generate,args=(tmp_path,preview,started,release,queue))
    process.start()
    try:
        assert started.wait(8)
        result=generate(controller,preview)
        assert result['state']=='generating'
        assert result['owner']['currentProcess'] is False and result['owner']['liveness']=='unknown'
        process.kill();process.join(5)
        after=DraftController(Store(tmp_path),lambda *a:pytest.fail('no repeat'),metadata(timeoutSeconds=15))
        assert generate(after,preview)['state']=='generating'
        assert after.get(preview['id'])['owner']['liveness']=='unknown'
    finally:
        # A killed multiprocessing.Event waiter may retain its semaphore.
        # Never touch that test IPC object after killing its owner.
        if process.is_alive():process.kill();process.join(5)


def test_readonly_reconnect_requires_no_provider_and_preserves_process_identity(tmp_path):
    configured=DraftController(Store(tmp_path),lambda *a:[definition()],metadata())
    preview=configured.create('goal')
    readonly=DraftController(Store(tmp_path))
    assert readonly.get(preview['id'])==preview
    with pytest.raises(DraftError,match='AI_DRAFT_PROVIDER_REQUIRED'):readonly.create('goal')
    with pytest.raises(DraftError,match='AI_DRAFT_PROVIDER_REQUIRED'):generate(readonly,preview)
    ready=generate(configured,preview)
    assert ready['owner']['currentProcess'] is True
    assert readonly.get(preview['id'])['owner']['currentProcess'] is True
    second=configured.create('another')
    assert readonly.cancel(second['id'])['state']=='cancelled'


def test_provider_exception_is_generic_without_raw_persistence(tmp_path):
    def sender(*args):raise ValueError('SECRET_RAW_PROVIDER_RESPONSE')
    controller=DraftController(Store(tmp_path),sender,metadata())
    preview=controller.create('goal')
    result=generate(controller,preview)
    assert result['state']=='failed' and result['error']=='DRAFT_PROVIDER_FAILED'
    assert 'SECRET_RAW_PROVIDER_RESPONSE' not in json.dumps(result)
    with controller.store.connection() as con:
        body=con.execute('SELECT body FROM ai_drafts WHERE id=?',(preview['id'],)).fetchone()[0]
    assert 'SECRET_RAW_PROVIDER_RESPONSE' not in body
    assert 'result' not in result


def test_three_normalized_drafts_and_preview_hash_covers_metadata(tmp_path):
    candidate=definition();candidate.pop('risk')
    controller=DraftController(Store(tmp_path),lambda *a:[copy.deepcopy(candidate) for _ in range(3)],metadata())
    preview=controller.create('goal')
    expected=digest({key:preview[key] for key in ('id','request','userPrompt','metadata','schemaVersion','consentVersion')})
    assert preview['promptHash']==expected
    ready=generate(controller,preview)
    assert len(ready['result']['candidates'])==3
    assert all(item['risk']=={'stopLossPct':None,'takeProfitPct':None} for item in ready['result']['candidates'])


@pytest.mark.parametrize('stage',['watcher_start','pre_send_read'])
def test_known_unsent_setup_failure_is_terminal(tmp_path,monkeypatch,stage):
    calls=[]
    controller=DraftController(Store(tmp_path),lambda *a:calls.append(1) or [definition()],metadata())
    preview=controller.create('goal')
    with monkeypatch.context() as patch:
        if stage=='watcher_start':
            def fail_start(*args):raise RuntimeError('PRIVATE_THREAD_ERROR')
            patch.setattr(threading.Thread,'start',fail_start)
        else:
            def fail_read(*args):raise RuntimeError('PRIVATE_READ_ERROR')
            patch.setattr(controller,'get',fail_read)
        result=generate(controller,preview)
    assert result['state']=='failed'
    assert result['error']==('DRAFT_START_FAILED' if stage=='watcher_start' else 'DRAFT_CANCEL_STATE_UNAVAILABLE')
    assert not calls and 'PRIVATE_' not in json.dumps(result)
    assert generate(controller,preview)==result


def test_known_unsent_failure_with_failed_persistence_remains_uncertain(tmp_path,monkeypatch):
    calls=[]
    controller=DraftController(Store(tmp_path),lambda *a:calls.append(1) or [definition()],metadata())
    preview=controller.create('goal');original=controller._write
    def fail_finalization(con,value):
        if value['state']=='failed':raise RuntimeError('PRIVATE_DISK_ERROR')
        return original(con,value)
    with monkeypatch.context() as patch:
        def fail_start(*args):raise RuntimeError('PRIVATE_THREAD_ERROR')
        patch.setattr(threading.Thread,'start',fail_start)
        patch.setattr(controller,'_write',fail_finalization)
        with pytest.raises(DraftError,match='DRAFT_STORAGE_ERROR') as error:generate(controller,preview)
    assert error.value.__context__ is None
    assert controller.get(preview['id'])['state']=='generating'
    assert generate(controller,preview)['state']=='generating' and not calls


@pytest.mark.parametrize('mutation',[
    'failed_without_error','cancelled_without_request','generating_without_owner',
    'preview_with_error','preview_with_owner','preview_with_cancel',
    'ready_with_error','ready_with_cancel','ready_without_owner','boolean_schema_version',
])
def test_persisted_state_field_invariants(tmp_path,mutation):
    controller=DraftController(Store(tmp_path),lambda *a:[definition()],metadata())
    preview=controller.create('goal')
    if mutation.startswith('ready') or mutation=='failed_without_error':generate(controller,preview)
    with controller.store.connection() as con:
        body=json.loads(con.execute('SELECT body FROM ai_drafts WHERE id=?',(preview['id'],)).fetchone()[0])
        if mutation=='failed_without_error':body.update(state='failed');body.pop('result')
        elif mutation=='cancelled_without_request':body.update(state='cancelled')
        elif mutation=='generating_without_owner':body.update(state='generating')
        elif mutation in ('preview_with_error','ready_with_error'):body['error']='DRAFT_PROVIDER_FAILED'
        elif mutation=='preview_with_owner':body['owner']={'pid':1,'instance':'a'*32}
        elif mutation in ('preview_with_cancel','ready_with_cancel'):body['cancelRequested']=True
        elif mutation=='ready_without_owner':body.pop('owner')
        else:
            body['schemaVersion']=True
            body['promptHash']=digest({key:body[key] for key in ('id','request','userPrompt','metadata','schemaVersion','consentVersion')})
        con.execute('UPDATE ai_drafts SET state=?,body=? WHERE id=?',(body['state'],canonical(body),preview['id']))
    with pytest.raises(DraftError,match='DRAFT_INTEGRITY_ERROR') as error:controller.get(preview['id'])
    assert error.value.__context__ is None
