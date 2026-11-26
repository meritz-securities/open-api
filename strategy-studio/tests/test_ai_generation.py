"""Real fake-CLI processes and real Store/engine; no model or authentication call."""
import dataclasses
import json
import multiprocessing
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from runtime.ai_cli_provider import ProviderConfig
from runtime.service import StrategyRuntime
from runtime.storage import digest
from test_ai_requests import definition

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='optional CLI is POSIX-only')


def fake(tmp_path, sleep=0):
    exe=tmp_path/'fake-cli'
    marker=tmp_path/'model-starts'
    candidate=definition(); candidate['name']='generated'
    exe.write_text(f'''#!{sys.executable}
import sys,time,json,os
if sys.argv[1:]==['--version']:
 print('2.1.322 (Claude Code)');sys.exit(0)
with open({str(marker)!r},'a') as f:f.write('started\\n')
with open({str(marker.with_suffix('.pid'))!r},'w') as f:f.write(str(os.getpid()))
sys.stdin.read()
time.sleep({sleep})
print({json.dumps({'is_error':False,'structured_output':{'candidates':[candidate]}})!r})
''')
    exe.chmod(0o700)
    return ProviderConfig(str(exe),'FAKE_KEY',timeout_seconds=10),marker


def setup(tmp_path, sleep=0):
    config,marker=fake(tmp_path,sleep)
    runtime=StrategyRuntime(tmp_path/'store',ai_provider=config)
    revision=runtime.save_strategy(definition())
    data=runtime.import_data([{'date':f'2025-01-0{i+1}','open':p,'high':p,'low':p,'close':p} for i,p in enumerate([10,20,30,40])],{'kind':'imported','label':'private-account-label'})
    baseline=runtime.run(revision['id'],data['id'])
    request=runtime.create_ai_request(baseline['id'],'reduce drawdown')['request']['id']
    approved=runtime.preview_ai_generation(request)
    return runtime,baseline,request,approved,marker


def invoke(runtime,request,approved):
    return runtime.generate_ai_candidates(request,approved['promptHash'],approved['consentVersion'])


def wait_file(path, diagnostic=None):
    deadline=time.monotonic()+5
    while not path.exists() and time.monotonic()<deadline:time.sleep(.01)
    assert path.exists(), diagnostic() if diagnostic else 'fixture marker did not appear'


def test_generation_common_submission_and_retry(tmp_path):
    runtime,baseline,request,approved,marker=setup(tmp_path)
    assert 'FAKE_KEY' not in json.dumps(approved)
    assert str(tmp_path) not in json.dumps(approved)
    assert 'private-account-label' not in approved['prompt']
    result=invoke(runtime,request,approved)
    assert result['status']=='succeeded', {'generation':result.get('generation'),'job':result.get('job')}
    assert result['submission']['origin']=='local-cli-generated'
    assert result['submission']['modelInvocationVerified'] is False
    assert result['generation']['validatedOutputReceived'] is True
    child=runtime.get_run(result['attempts'][0]['runId'])
    assert child['manifest']['dataHash']==baseline['manifest']['dataHash']
    assert child['settings']==baseline['settings']
    assert runtime.get_run(baseline['id'])==baseline
    assert invoke(runtime,request,approved)==result
    assert marker.read_text().splitlines()==['started']


def test_configuration_and_consent_are_server_only(tmp_path):
    runtime,_,request,approved,marker=setup(tmp_path)
    for changes in ({'promptHash':'bad'}, {'consentVersion':'bad'}, {'api_key':'x'}, {'executable':'/bin/sh'}):
        args={'requestId':request,'promptHash':approved['promptHash'],'consentVersion':approved['consentVersion'],**changes}
        with pytest.raises((ValueError,TypeError)):
            runtime.dispatch('generate_ai_candidates',args)
    assert not marker.exists()
    runtime._ai_provider=None
    with pytest.raises(ValueError,match='PROVIDER_NOT_CONFIGURED'):
        runtime.preview_ai_generation(request)


def test_ready_busy_retry_without_regeneration(tmp_path):
    runtime,_,request,approved,marker=setup(tmp_path)
    with runtime.store.execution_lock():
        first=invoke(runtime,request,approved)
    assert first['status']=='ready'
    other=StrategyRuntime(runtime.store.root,ai_provider=runtime._ai_provider)
    assert invoke(other,request,approved)['status']=='succeeded'
    assert marker.read_text().splitlines()==['started']


def test_cross_instance_cancel_and_no_revision(tmp_path):
    runtime,_,request,approved,marker=setup(tmp_path,sleep=5)
    result=[]
    worker=threading.Thread(target=lambda:result.append(invoke(runtime,request,approved)))
    worker.start();wait_file(marker, lambda: {'generation':runtime.get_ai_request(request).get('generation'), 'job':runtime.get_ai_request(request).get('job')})
    other=StrategyRuntime(runtime.store.root,ai_provider=runtime._ai_provider)
    assert other.get_ai_request(request)['status']=='generating' # engine recovery must not touch it
    other.cancel_ai_request(request)
    worker.join(5);assert not worker.is_alive()
    assert result[0]['status']=='cancelled'
    assert len(runtime.list_strategies()['strategies'])==1
    assert invoke(runtime,request,approved)['status']=='cancelled'
    assert marker.read_text().splitlines()==['started']


def test_cancel_at_submission_boundary(tmp_path,monkeypatch):
    from runtime import ai_generation
    runtime,_,request,approved,marker=setup(tmp_path)
    original=ai_generation.generate_candidates
    def complete_then_cancel(*args):
        candidates=original(*args)
        runtime.cancel_ai_request(request)
        return candidates
    monkeypatch.setattr(ai_generation,'generate_candidates',complete_then_cancel)
    assert invoke(runtime,request,approved)['status']=='cancelled'
    assert len(runtime.list_runs()['runs'])==1


def test_external_submission_cannot_race_local_generation(tmp_path):
    runtime,_,request,approved,marker=setup(tmp_path,sleep=.4)
    results=[]
    thread=threading.Thread(target=lambda:results.append(invoke(runtime,request,approved)))
    thread.start();wait_file(marker)
    with pytest.raises(ValueError,match='로컬 생성'):
        runtime.submit_ai_candidates(request,[definition()])
    with pytest.raises(ValueError,match='Another AI generation'):
        invoke(runtime,request,approved)
    thread.join(5)
    assert results[0]['status']=='succeeded'


def _crash_after_claim(root,request):
    runtime=StrategyRuntime(root)
    with runtime.store.ai_generation_lock():
        runtime.store.put('ai-generation',request,{'id':request,'status':'generating','promptHash':'old'})
        os._exit(0)


def test_crashed_generation_recovered_without_model(tmp_path):
    runtime,_,request,approved,marker=setup(tmp_path)
    child=multiprocessing.get_context('spawn').Process(target=_crash_after_claim,args=(runtime.store.root,request))
    child.start();child.join(10);assert child.exitcode==0
    result=invoke(runtime,request,approved)
    assert result['status']=='interrupted'
    assert not marker.exists()


def test_ready_cancel_prevents_submit(tmp_path):
    runtime,_,request,approved,marker=setup(tmp_path)
    with runtime.store.execution_lock():assert invoke(runtime,request,approved)['status']=='ready'
    runtime.cancel_ai_request(request)
    assert invoke(runtime,request,approved)['status']=='cancelled'
    assert len(runtime.list_runs()['runs'])==1


def _generate_process(root, config, request, approved, result_path):
    runtime=StrategyRuntime(root,ai_provider=config)
    result=invoke(runtime,request,approved)
    Path(result_path).write_text(result['status'])


def test_real_process_competition_and_cancel(tmp_path):
    runtime,_,request,approved,marker=setup(tmp_path,sleep=5)
    result_path=tmp_path/'process-result'
    process=multiprocessing.get_context('spawn').Process(target=_generate_process,
        args=(runtime.store.root,runtime._ai_provider,request,approved,result_path))
    process.start()
    try:
        wait_file(marker)
        # A competing Store startup acquires the engine lock but cannot recover
        # a live generation, whose ownership is a different OS lock.
        other=StrategyRuntime(runtime.store.root,ai_provider=runtime._ai_provider)
        assert other.get_ai_request(request)['status']=='generating'
        with pytest.raises(ValueError,match='Another AI generation'):
            invoke(other,request,approved)
        other.cancel_ai_request(request)
        process.join(8)
        assert process.exitcode==0
        assert result_path.read_text()=='cancelled'
        assert marker.read_text().splitlines()==['started']
    finally:
        if process.is_alive():process.kill();process.join(5)


def test_provider_failure_is_terminal_no_second_charge(tmp_path):
    runtime,_,request,approved,marker=setup(tmp_path)
    exe=Path(runtime._ai_provider.executable)
    source=exe.read_text().replace('sys.stdin.read()', 'sys.stdin.read();sys.exit(7)')
    exe.write_text(source)
    first=invoke(runtime,request,approved)
    assert first['status']=='failed'
    assert first['generation']['error']=='PROVIDER_FAILED'
    assert first['generation']['modelInvocationVerified'] is False
    assert invoke(runtime,request,approved)==first
    assert marker.read_text().splitlines()==['started']


def test_cancel_after_ready_before_common_submit(tmp_path,monkeypatch):
    from runtime import ai_requests
    runtime,_,request,approved,_=setup(tmp_path)
    original=ai_requests.submit
    def intercept(service,ident,candidates,**kwargs):
        service.cancel_ai_request(ident)
        return original(service,ident,candidates,**kwargs)
    monkeypatch.setattr(ai_requests,'submit',intercept)
    result=invoke(runtime,request,approved)
    assert result['status']=='cancelled'
    assert result['job'] is None
    assert len(runtime.list_strategies()['strategies'])==1


def test_changed_budget_requires_new_consent(tmp_path):
    runtime,_,request,approved,marker=setup(tmp_path)
    runtime._ai_provider=dataclasses.replace(runtime._ai_provider,budget_usd=.5)
    with pytest.raises(ValueError,match='다시 확인'):
        invoke(runtime,request,approved)
    assert not marker.exists()


def test_capability_is_configuration_not_auth_evidence(tmp_path):
    runtime,_,_,_,_=setup(tmp_path)
    capability=runtime.capabilities()['aiGeneration']
    assert capability['available'] is True
    assert capability['modelInvocationVerified'] is False
    assert capability['scope']=='existing-successful-dsl-improvement'
    assert 'FAKE_KEY' not in json.dumps(capability)
    assert str(tmp_path) not in json.dumps(capability)
    runtime._ai_provider=None
    assert runtime.capabilities()['aiGeneration']['reason']=='PROVIDER_NOT_CONFIGURED'


def test_killed_owner_during_fake_cli_does_not_regenerate(tmp_path):
    import signal
    runtime,_,request,approved,marker=setup(tmp_path,sleep=30)
    process=multiprocessing.get_context('spawn').Process(target=_generate_process,
        args=(runtime.store.root,runtime._ai_provider,request,approved,tmp_path/'never-result'))
    process.start()
    try:
        wait_file(marker.with_suffix('.pid'))
        process.kill();process.join(5)
        assert process.exitcode is not None
        restarted=StrategyRuntime(runtime.store.root,ai_provider=runtime._ai_provider)
        result=invoke(restarted,request,approved)
        assert result['status']=='interrupted'
        assert marker.read_text().splitlines()==['started']
        assert len(restarted.list_runs()['runs'])==1
    finally:
        if process.is_alive():process.kill();process.join(5)
        if marker.with_suffix('.pid').exists():
            try:os.killpg(int(marker.with_suffix('.pid').read_text()),signal.SIGKILL)
            except ProcessLookupError:pass


def test_external_cancel_busy_engine_has_no_side_effect(tmp_path):
    runtime,_,request,_,marker=setup(tmp_path)
    before=runtime.get_ai_request(request)
    with runtime.store.execution_lock():
        with pytest.raises(ValueError,match='Another local run is active'):
            runtime.cancel_ai_request(request)
    assert runtime.get_ai_request(request)==before
    assert runtime.store.list('ai-generation-cancel')==[]
    result=runtime.submit_ai_candidates(request,[definition()])
    assert result['status']=='succeeded', {'generation':result.get('generation'),'job':result.get('job')}
    assert result['submission']['origin']=='external-host-submitted'
    assert not marker.exists()


def test_generic_cancel_cannot_acknowledge_internal_generation_key(tmp_path,monkeypatch):
    from runtime import ai_requests
    runtime,_,request,approved,marker=setup(tmp_path)
    original=ai_requests.submit
    def boundary(service,ident,candidates,**kwargs):
        with pytest.raises(ValueError,match='cancel_ai_request'):
            service.dispatch('cancel',{'jobId':'generation-'+ident})
        service.cancel_ai_request(ident)
        return original(service,ident,candidates,**kwargs)
    monkeypatch.setattr(ai_requests,'submit',boundary)
    assert invoke(runtime,request,approved)['status']=='cancelled'
    assert invoke(runtime,request,approved)['status']=='cancelled'
    assert len(runtime.list_runs()['runs'])==1
    assert marker.read_text().splitlines()==['started']


def test_watcher_read_failure_is_not_user_cancellation(tmp_path,monkeypatch):
    from runtime import ai_generation
    runtime,_,request,approved,marker=setup(tmp_path,sleep=5)
    original=ai_generation.cancelled
    once=threading.Event()
    main_thread=threading.current_thread()
    def fail_in_watcher(service,ident):
        if threading.current_thread() is not main_thread and marker.exists() and not once.is_set():
            once.set()
            raise ValueError('PRIVATE_DATABASE_ERROR')
        return original(service,ident)
    monkeypatch.setattr(ai_generation,'cancelled',fail_in_watcher)
    result=invoke(runtime,request,approved)
    assert once.is_set()
    assert result['status']=='failed', result['generation']
    assert result['generation']['error']=='CANCEL_STATE_UNAVAILABLE'
    assert result['cancellationRequested'] is False
    assert 'PRIVATE_DATABASE_ERROR' not in json.dumps(result)
    assert invoke(runtime,request,approved)==result
    assert len(runtime.list_runs()['runs'])==1


@pytest.mark.parametrize('target',['ready','failed','generating','interrupted'])
def test_ready_generation_rejects_uncontracted_transitions(tmp_path,target):
    runtime,_,request,approved,_=setup(tmp_path)
    with runtime.store.execution_lock():assert invoke(runtime,request,approved)['status']=='ready'
    old=runtime.store.get('ai-generation',request)
    with pytest.raises(ValueError,match='Invalid generation transition'):
        runtime.store.transition_ai_generation(request,{**old,'status':target})
    assert runtime.store.get('ai-generation',request)==old
    runtime.cancel_ai_request(request)
    assert runtime.store.get('ai-generation',request)['status']=='cancelled'


def test_control_failure_at_ready_preserves_candidates_without_recharge(tmp_path,monkeypatch):
    from runtime import ai_requests
    runtime,_,request,approved,marker=setup(tmp_path)
    original=ai_requests.submit
    def boundary(service,ident,candidates,**kwargs):
        event=kwargs['_generation_event']
        event.control_error='CANCEL_STATE_UNAVAILABLE';event.set()
        return original(service,ident,candidates,**kwargs)
    monkeypatch.setattr(ai_requests,'submit',boundary)
    with pytest.raises(ValueError,match='CANCEL_STATE_UNAVAILABLE'):
        invoke(runtime,request,approved)
    result=runtime.get_ai_request(request)
    assert result['status']=='ready' and result['job'] is None
    assert result['cancellationRequested'] is False
    monkeypatch.setattr(ai_requests,'submit',original)
    assert invoke(runtime,request,approved)['status']=='succeeded'
    assert marker.read_text().splitlines()==['started']


def test_control_failure_during_execution_has_control_stage(tmp_path,monkeypatch):
    runtime,_,request,approved,_=setup(tmp_path)
    original=runtime._run_locked
    def interrupt(*args,**kwargs):
        event=kwargs['cancel']
        event.control_error='CANCEL_STATE_UNAVAILABLE';event.set()
        return original(*args,**kwargs)
    monkeypatch.setattr(runtime,'_run_locked',interrupt)
    result=invoke(runtime,request,approved)
    assert result['status']=='failed', result['job']
    assert result['job']['stage']=='control'
    assert result['job']['error']=='CANCEL_STATE_UNAVAILABLE'
    assert result['cancellationRequested'] is False


def test_late_control_error_preserves_completed_result(tmp_path,monkeypatch):
    runtime,_,request,approved,_=setup(tmp_path)
    original=runtime.store.put
    def persist_then_fault(kind,ident,value):
        result=original(kind,ident,value)
        if kind=='ai-result':
            assert all(attempt['status']=='succeeded' for attempt in value['attempts'])
            assert value['comparison']
            event=runtime._active['ai-'+request]
            event.control_error='CANCEL_STATE_UNAVAILABLE'
            event.set()  # Exact real-watcher ordering from independent R3 probe.
        return result
    monkeypatch.setattr(runtime.store,'put',persist_then_fault)
    result=invoke(runtime,request,approved)
    assert result['status']=='succeeded', result['job']
    assert result['job']['outcome']=='completed'
    assert 'error' not in result['job']
    assert result['result']['comparison']
    assert all(attempt['status']=='succeeded' for attempt in result['attempts'])
    assert runtime.get_ai_request(request)==result


def test_control_error_does_not_replace_baseexception_interrupted(tmp_path,monkeypatch):
    runtime,_,request,approved,_=setup(tmp_path)
    def interrupt(*args,**kwargs):
        event=kwargs['cancel']
        event.control_error='CANCEL_STATE_UNAVAILABLE'
        event.set()
        raise KeyboardInterrupt()
    monkeypatch.setattr(runtime,'_run_locked',interrupt)
    with pytest.raises(KeyboardInterrupt):
        invoke(runtime,request,approved)
    result=runtime.get_ai_request(request)
    assert result['status']=='interrupted', result['job']
    assert result['job']['stage']=='execution'
    assert result['job']['error']=='후보 제출 처리가 중단되었습니다.'
    assert result['generation']['status']=='ready'
    assert runtime._active=={}


def test_late_user_cancel_preserves_completed_result(tmp_path,monkeypatch):
    runtime,_,request,approved,_=setup(tmp_path)
    original=runtime.store.put
    def persist_then_cancel(kind,ident,value):
        result=original(kind,ident,value)
        if kind=='ai-result':runtime.cancel_ai_request(request)
        return result
    monkeypatch.setattr(runtime.store,'put',persist_then_cancel)
    result=invoke(runtime,request,approved)
    assert result['status']=='succeeded', result['job']
    assert result['job']['outcome']=='completed'
    assert result['cancellationRequested'] is True  # Requested, too late to interrupt work.
    assert result['result']['comparison']


@pytest.mark.parametrize('control_failure',[False,True])
def test_cancellation_before_comparison_still_cuts_work_short(tmp_path,monkeypatch,control_failure):
    runtime,_,request,approved,_=setup(tmp_path)
    original=runtime.store.put
    def persist_then_cancel(kind,ident,value):
        result=original(kind,ident,value)
        if kind=='ai-attempt':
            assert value['status']=='succeeded'
            event=runtime._active['ai-'+request]
            if control_failure:event.control_error='CANCEL_STATE_UNAVAILABLE'
            event.set()
        return result
    monkeypatch.setattr(runtime.store,'put',persist_then_cancel)
    monkeypatch.setattr(runtime,'compare',lambda *a:pytest.fail('comparison must remain skipped'))
    result=invoke(runtime,request,approved)
    assert result['status']==('failed' if control_failure else 'cancelled'), result['job']
    assert result['result']['comparison'] is None
    assert result['result']['unattemptedCount']==0
    assert all(a['status']=='succeeded' for a in result['attempts'])
    if control_failure:
        assert result['job']['stage']=='control'
        assert result['job']['error']=='CANCEL_STATE_UNAVAILABLE'


def test_result_persistence_failure_replaces_provisional_completion(tmp_path,monkeypatch):
    runtime,_,request,approved,_=setup(tmp_path)
    original=runtime.store.put
    def fail_result(kind,ident,value):
        if kind=='ai-result':raise ValueError('fixture failure')
        return original(kind,ident,value)
    monkeypatch.setattr(runtime.store,'put',fail_result)
    result=invoke(runtime,request,approved)
    assert result['status']=='failed',result['job']
    assert result['job']['outcome']=='failed'
    assert result['job']['stage']=='persistence'
    assert result['result'] is None
    assert all(a['status']=='succeeded' for a in result['attempts'])
