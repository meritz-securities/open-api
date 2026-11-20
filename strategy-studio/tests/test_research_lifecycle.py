"""Independent shared-engine research expectations and one-attempt recovery."""
import copy
import json
import threading
import pytest
from runtime.service import StrategyRuntime
from runtime.storage import digest, encode_body
from runtime.ai_improvement_context import prompt as http_prompt
from runtime.ai_requests import source as request_source
from test_research_setup import setup


def prepared(tmp_path, prices=(10,20,40,100,110,90)):
    service,source,bars=setup(tmp_path,prices)
    ident=service.create_research(source,'reduce loss',.5)['session']['id']
    value=service.prepare_research_training(ident)
    return service,source,ident,value


def submit(service, ident, quantities=(2,3)):
    value=service.get_research(ident)
    definition=service.get_strategy(value['session']['revisionId'])['definition']
    candidates=[{**definition,'name':f'q{q}','quantity':q} for q in quantities]
    return service.submit_ai_candidates(value['training']['aiRequestId'],candidates)


def test_full_fresh_flow_training_winner_only_and_independent_accounts(tmp_path):
    service,source,ident,view=prepared(tmp_path)
    assert view['training']['jobStatus']=='succeeded'
    assert view['training']['bindingStatus']=='bound'
    baseline=service.get_run(view['training']['baselineRunId'])
    assert baseline['report']['metrics']['finalEquity']==1020
    assert len(service.list_runs()['runs'])==1
    assert service.prepare_research_training(ident)==view
    submit(service,ident)
    freeze=service.freeze_research(ident)
    pool=freeze['frozen']['candidates']
    assert [c['metrics']['finalEquity'] for c in pool]==[1020,1040,1060]
    assert [c['selected'] for c in pool]==[False,False,True]
    assert [c['evaluationEligible'] for c in pool]==[True,False,True]
    assert freeze['session']['split']['holdout']['datasetId'] is None
    assert len(service.store.list('dataset'))==2
    result=service.evaluate_research(ident)
    assert result['evaluation']['status']=='succeeded'
    assert [s['metrics']['finalEquity'] for s in result['evaluation']['slots']]==[980,940]
    assert result['frozen']['selectedRevisionId']==pool[2]['revisionId']
    assert len(service.list_runs()['runs'])==5 # train baseline +2train +2heldout
    assert len(service.store.list('dataset'))==3
    assert service.evaluate_research(ident)==result
    assert StrategyRuntime(tmp_path).evaluate_research(ident)==result
    assert service.get_strategy(source['revisionId'])['definition']['quantity']==1
    assert [r['settings']['initialCash'] for r in service.store.list('run')]==[1000]*5


def test_baseline_tie_evaluates_one_slot_and_other_requests_disclosed(tmp_path):
    service,_,ident,view=prepared(tmp_path)
    submit(service,ident,(1,))
    service.create_ai_request(view['training']['baselineRunId'],'unrelated experiment')
    frozen=service.freeze_research(ident)['frozen']
    assert frozen['selectedRevisionId']==view['session']['revisionId']
    assert frozen['otherAiRequestsOnTrainingRunCount']==1
    assert len(frozen['evaluationSlots'])==1
    assert len(service.evaluate_research(ident)['evaluation']['slots'])==1


def test_prompts_are_train_only_and_suffix_independent(tmp_path):
    service,_,ident,view=prepared(tmp_path/'a')
    other,_,other_id,other_view=prepared(tmp_path/'b',prices=(10,20,40,999999,888888,777777))
    def prompt(runtime, research):
        owned=runtime.store.get('ai-request',research['training']['aiRequestId'])
        run,revision,data=request_source(runtime,owned)
        return http_prompt(owned,run,revision,data)
    assert prompt(service,view)==prompt(other,other_view)
    assert '999999' not in prompt(other,other_view)
    original=service.get_strategy(view['session']['revisionId'])['definition']
    for runtime,research_id in [(service,ident),(other,other_id)]:submit(runtime,research_id)
    assert service.freeze_research(ident)['frozen']['candidates'][2]['metrics']==other.freeze_research(other_id)['frozen']['candidates'][2]['metrics']
    changed,_,_,changed_view=prepared(tmp_path/'c',prices=(10,30,40,100,110,90))
    assert prompt(service,view)!=prompt(changed,changed_view)


def test_any_training_failure_blocks_freeze_and_preserves_attempts(tmp_path):
    service,_,ident,view=prepared(tmp_path)
    original=service.get_strategy(view['session']['revisionId'])['definition']
    outcome=service.submit_ai_candidates(view['training']['aiRequestId'],[original,{**original,'quantity':True}])
    assert [a['status'] for a in outcome['attempts']]==['succeeded','failed']
    assert service.get_research(ident)['eligibility']['canFreeze'] is False
    with pytest.raises(ValueError,match='NOT_COMPLETE'):service.freeze_research(ident)
    assert len(service.store.list('dataset'))==2
    with pytest.raises(ValueError,match='POOL_NOT_FROZEN'):service.evaluate_research(ident)


def test_occupied_owned_request_is_terminal_and_never_cancelled_as_foreign(tmp_path,monkeypatch):
    service,source,_=setup(tmp_path)
    ident=service.create_research(source,'owned purpose',.5)['session']['id']
    original=service.create_ai_request
    def intercept(runId,purpose,requestId=None):
        original(runId,'foreign purpose',requestId)
        return original(runId,purpose,requestId)
    monkeypatch.setattr(service,'create_ai_request',intercept)
    view=service.prepare_research_training(ident)
    assert view['training']['bindingStatus']=='training_binding_failed'
    foreign=service.get_ai_request(view['training']['aiRequestId'])
    assert foreign['status']=='awaiting_candidates'
    assert service.cancel_research(ident,'training')['cancellation']['forwarded'] is False
    assert service.get_ai_request(view['training']['aiRequestId'])==foreign
    service.prepare_research_training(ident)
    assert len(service.list_runs()['runs'])==1


def test_existing_same_payload_request_is_idempotently_bound(tmp_path,monkeypatch):
    service,source,_=setup(tmp_path)
    ident=service.create_research(source,'purpose',.5)['session']['id']
    original=service.create_ai_request
    def twice(*args,**kwargs):
        original(*args,**kwargs)
        return original(*args,**kwargs)
    monkeypatch.setattr(service,'create_ai_request',twice)
    view=service.prepare_research_training(ident)
    assert view['training']['bindingStatus']=='bound'
    assert view['training']['jobStatus']=='succeeded'
    assert len(service.store.list('ai-request'))==1


def test_successful_baseline_crash_rebinds_without_new_run(tmp_path,monkeypatch):
    service,source,_=setup(tmp_path)
    ident=service.create_research(source,'purpose',.5)['session']['id']
    original=service._run_locked
    def stop(*args,**kwargs):
        original(*args,**kwargs)
        raise SystemExit('crash before phase finalize')
    monkeypatch.setattr(service,'_run_locked',stop)
    with pytest.raises(SystemExit):service.prepare_research_training(ident)
    reopened=StrategyRuntime(tmp_path)
    view=reopened.prepare_research_training(ident)
    assert view['training']['jobStatus']=='interrupted'
    assert view['training']['baselineStatus']=='succeeded'
    assert view['training']['bindingStatus']=='bound'
    assert len(reopened.list_runs()['runs'])==1


def test_holdout_crash_after_first_slot_never_reruns_remaining(tmp_path,monkeypatch):
    service,_,ident,_=prepared(tmp_path);submit(service,ident);service.freeze_research(ident)
    original=service._run_locked
    def stop(*args,**kwargs):
        original(*args,**kwargs)
        raise SystemExit('crash after first saved holdout')
    monkeypatch.setattr(service,'_run_locked',stop)
    with pytest.raises(SystemExit):service.evaluate_research(ident)
    reopened=StrategyRuntime(tmp_path)
    result=reopened.evaluate_research(ident)
    assert result['evaluation']['jobStatus']=='interrupted'
    assert [s['status'] for s in result['evaluation']['slots']]==['succeeded','not_run']
    assert result['evaluation']['slots'][0]['metrics']['finalEquity']==980
    assert len(reopened.list_runs()['runs'])==4
    assert reopened.evaluate_research(ident)==result


def test_cancel_intent_blocks_freeze_even_if_forward_busy_and_ai_later_succeeds(tmp_path,monkeypatch):
    service,_,ident,view=prepared(tmp_path)
    monkeypatch.setattr(service,'cancel_ai_request',lambda *_:(_ for _ in ()).throw(ValueError('Another local run is active')))
    response=service.cancel_research(ident,'training')
    assert response['cancellation']=={'phase':'training','requested':True,'forwarded':False,'effective':True,'reason':'CANCELLATION_REQUESTED'}
    submit(service,ident)
    view=service.get_research(ident)
    assert 'TRAINING_CANCELLED' in view['eligibility']['reasons']['freeze']
    with pytest.raises(ValueError,match='TRAINING_CANCELLED'):service.freeze_research(ident)


def test_late_cancel_does_not_change_frozen_or_successful_evaluation(tmp_path):
    service,_,ident,_=prepared(tmp_path);submit(service,ident);frozen=service.freeze_research(ident)['frozen']
    response=service.cancel_research(ident,'training')
    assert response['cancellation']['effective'] is False
    assert response['cancellation']['reason']=='ALREADY_FROZEN'
    result=service.evaluate_research(ident)['evaluation']
    after=service.cancel_research(ident,'evaluation')
    assert after['cancellation']['reason']=='EVALUATION_FINISHED'
    assert after['frozen']==frozen and after['evaluation']==result


def test_pre_evaluation_cancel_creates_no_heldout_dataset_or_job(tmp_path):
    service,_,ident,_=prepared(tmp_path);submit(service,ident);service.freeze_research(ident)
    service.cancel_research(ident,'evaluation')
    with pytest.raises(ValueError,match='CANCELLED'):service.evaluate_research(ident)
    assert len(service.store.list('dataset'))==2
    assert service.get_research(ident)['evaluation']['jobStatus'] is None


def test_reserved_slot_foreign_collision_rejected_before_engine_active(tmp_path,monkeypatch):
    service,source,_=setup(tmp_path);plan=service.create_research(source,'purpose',.5)['session']
    service.store.put('run',plan['trainingRunId'],{'id':plan['trainingRunId'],'status':'failed'})
    monkeypatch.setattr(service,'_calculate',lambda *a,**k:pytest.fail('no engine'))
    with pytest.raises(ValueError,match='occupied'):service.prepare_research_training(plan['id'])
    assert service._active=={}
    assert not service.store.list('validation-job')


def test_failed_run_keeps_research_metadata_and_no_retry(tmp_path,monkeypatch):
    service,source,_=setup(tmp_path);ident=service.create_research(source,'purpose',.5)['session']['id']
    monkeypatch.setattr(service,'_calculate',lambda *a,**k:(_ for _ in ()).throw(ValueError('failure')))
    view=service.prepare_research_training(ident)
    run=service.get_run(view['training']['baselineRunId'])
    assert run['status']=='failed' and run['researchId']==ident and run['researchPhase']=='training' and run['researchSlot']==0
    assert service.list_runs()['runs'][0]['researchId']==ident
    assert service.prepare_research_training(ident)==view
    assert len(service.list_runs()['runs'])==1


@pytest.mark.parametrize('status',['failed','cancelled','interrupted'])
def test_cancel_finished_evaluation_without_result_is_ineffective(tmp_path,status):
    service,_,ident,_=prepared(tmp_path);submit(service,ident);view=service.freeze_research(ident)
    job_id=view['session']['evaluationJobId']
    service.store.put('validation-job',job_id,{'id':job_id,'status':status,'method':'research_evaluation'})
    response=service.dispatch('cancel_research',{'researchId':ident,'phase':'evaluation'})
    assert response['cancellation']['effective'] is False
    assert response['cancellation']['reason']=='EVALUATION_FINISHED'
    assert response['evaluation']['status']==status
    assert not service.store.list('research-cancel')


def test_version_drift_blocks_new_phase_but_keeps_saved_result_readable(tmp_path,monkeypatch):
    import runtime.research as research
    service,_,ident,_=prepared(tmp_path);submit(service,ident);service.freeze_research(ident)
    original=research._versions
    monkeypatch.setattr(research,'_versions',lambda:{**original(),'runtimeVersion':'different'})
    view=service.get_research(ident)
    assert view['eligibility']['canEvaluate'] is False
    assert 'RESEARCH_VERSION_CHANGED' in view['eligibility']['reasons']['evaluate']
    with pytest.raises(ValueError,match='VERSION_CHANGED'):service.evaluate_research(ident)
    assert len(service.store.list('dataset'))==2


def test_frozen_selection_cannot_be_swapped_to_other_candidate(tmp_path):
    service,_,ident,view=prepared(tmp_path);submit(service,ident);service.freeze_research(ident)
    value=service.store.get('research-freeze',ident)
    value['selectedRevisionId']=view['session']['revisionId']
    value['recordHash']=digest({k:v for k,v in value.items() if k!='recordHash'})
    with service.store.connection() as con:con.execute("UPDATE objects SET body=? WHERE kind='research-freeze' AND id=?",(encode_body(value),ident))
    with pytest.raises(ValueError,match='selection'):service.evaluate_research(ident)
    assert len(service.store.list('dataset'))==2


def test_training_deadline_preserves_failed_job_cancelled_slot_without_retry(tmp_path,monkeypatch):
    import runtime.research as research
    service,source,_=setup(tmp_path);ident=service.create_research(source,'purpose',.5)['session']['id']
    monkeypatch.setattr(research,'PHASE_TIMEOUT_SECONDS',.03)
    def stall(definition,data,settings,cancel):
        assert cancel.wait(2)
        raise ValueError('timeout fixture')
    monkeypatch.setattr(service,'_calculate',stall)
    view=service.prepare_research_training(ident)
    assert view['training']['jobStatus']=='failed'
    assert view['training']['baselineStatus']=='cancelled'
    assert not service.store.list('ai-request')
    assert not service._active
    assert service.prepare_research_training(ident)==view
    assert len(service.list_runs()['runs'])==1


def test_cancel_after_baseline_success_before_ai_binding_blocks_request(tmp_path,monkeypatch):
    service,source,_=setup(tmp_path);ident=service.create_research(source,'purpose',.5)['session']['id']
    original=service._run_locked
    def completed(*args,**kwargs):
        value=original(*args,**kwargs)
        service.cancel_research(ident,'training')
        return value
    monkeypatch.setattr(service,'_run_locked',completed)
    view=service.prepare_research_training(ident)
    assert view['training']['baselineStatus']=='succeeded'
    assert view['training']['jobStatus']=='succeeded'
    assert view['training']['cancellationRequested'] is True
    assert view['training']['bindingStatus']=='not_bound'
    assert not service.store.list('ai-request')
    assert 'TRAINING_CANCELLED' in view['eligibility']['reasons']['freeze']


def test_public_run_cannot_accept_reserved_execution_arguments(tmp_path,monkeypatch):
    service,source,_=setup(tmp_path)
    monkeypatch.setattr(service,'_calculate',lambda *a,**k:pytest.fail('no engine'))
    with pytest.raises(TypeError):service.dispatch('run',{**source,'runId':'a'*32,'researchId':'b'*32})
    assert service.list_runs()['runs']==[]


def test_repeat_prepare_while_owner_finalizes_child_does_not_nest_engine_lock(tmp_path,monkeypatch):
    service,source,_=setup(tmp_path);ident=service.create_research(source,'purpose',.5)['session']['id']
    original=service._run_locked
    observations=[]
    def saved_child(*args,**kwargs):
        result=original(*args,**kwargs)
        repeated=service.prepare_research_training(ident)
        observations.append(repeated['training']['jobStatus'])
        assert repeated['training']['aiRequestStatus'] is None
        assert repeated['eligibility']['canPrepareTraining'] is False
        return result
    monkeypatch.setattr(service,'_run_locked',saved_child)
    view=service.prepare_research_training(ident)
    assert observations==['running']
    assert view['training']['jobStatus']=='succeeded' and view['training']['bindingStatus']=='bound'
    assert len(service.list_runs()['runs'])==1


@pytest.mark.parametrize('cancel_first', [True, False])
def test_evaluation_result_cancel_first_commit_wins(tmp_path, monkeypatch, cancel_first):
    service,_,ident,_=prepared(tmp_path);submit(service,ident);service.freeze_research(ident)
    original=service.store._research_decision
    acknowledgements=[]
    def decision(research_id, action, value):
        if action!='result':return original(research_id,action,value)
        other=StrategyRuntime(tmp_path)
        if cancel_first:
            acknowledgements.append(other.cancel_research(ident,'evaluation')['cancellation'])
        result=original(research_id,action,value)
        if not cancel_first:
            acknowledgements.append(other.cancel_research(ident,'evaluation')['cancellation'])
        return result
    monkeypatch.setattr(service.store,'_research_decision',decision)
    view=service.evaluate_research(ident)
    assert acknowledgements[0]['effective'] is cancel_first
    assert acknowledgements[0]['reason']==('CANCELLATION_REQUESTED' if cancel_first else 'EVALUATION_FINISHED')
    assert view['evaluation']['status']==('cancelled' if cancel_first else 'succeeded')
    assert view['evaluation']['jobStatus']==view['evaluation']['status']
    assert [s['status'] for s in view['evaluation']['slots']]==['succeeded','succeeded']
    assert view['evaluation']['result']['cancellationWon'] is cancel_first
    assert [s['metrics']['finalEquity'] for s in view['evaluation']['slots']]==[980,940]
    restarted=StrategyRuntime(tmp_path)
    assert restarted.evaluate_research(ident)==view
    assert len(restarted.list_runs()['runs'])==5


def test_evaluation_cancel_before_freeze_cannot_poison_later_evaluation(tmp_path):
    service,_,ident,_=prepared(tmp_path)
    cancelled=service.cancel_research(ident,'evaluation')
    assert cancelled['cancellation']=={'phase':'evaluation','requested':False,'forwarded':False,
        'effective':False,'reason':'POOL_NOT_FROZEN'}
    assert not service.store.list('research-cancel')
    submit(service,ident);service.freeze_research(ident)
    assert service.evaluate_research(ident)['evaluation']['status']=='succeeded'


def test_evaluation_cancel_before_materialization_has_durable_result(tmp_path,monkeypatch):
    import runtime.research as research
    service,_,ident,_=prepared(tmp_path);submit(service,ident);service.freeze_research(ident)
    original=research._claim_phase
    def claimed(*args,**kwargs):
        value=original(*args,**kwargs)
        if args[2]=='evaluation':service.cancel_research(ident,'evaluation')
        return value
    monkeypatch.setattr(research,'_claim_phase',claimed)
    view=service.evaluate_research(ident)
    assert view['evaluation']['status']=='cancelled'
    assert view['evaluation']['datasetId'] is None
    assert view['evaluation']['result']['datasetId'] is None
    assert [s['status'] for s in view['evaluation']['result']['slots']]==['not_run','not_run']
    assert len(service.store.list('dataset'))==2
    assert len(service.list_runs()['runs'])==3
    assert StrategyRuntime(tmp_path).evaluate_research(ident)==view


@pytest.mark.parametrize('timeout', [True, False])
def test_evaluation_cooperative_stop_has_durable_partial_result(tmp_path,monkeypatch,timeout):
    import runtime.research as research
    service,_,ident,_=prepared(tmp_path);submit(service,ident);service.freeze_research(ident)
    if timeout:monkeypatch.setattr(research,'PHASE_TIMEOUT_SECONDS',.03)
    def stopped(definition,data,settings,cancel):
        if timeout:assert cancel.wait(2)
        else:service.cancel_research(ident,'evaluation')
        raise ValueError('stopped fixture')
    monkeypatch.setattr(service,'_calculate',stopped)
    view=service.evaluate_research(ident)
    assert view['evaluation']['status']==('failed' if timeout else 'cancelled')
    assert view['evaluation']['result']['status']==view['evaluation']['status']
    assert [s['status'] for s in view['evaluation']['slots']]==['cancelled','not_run']
    assert not service._active
    assert StrategyRuntime(tmp_path).evaluate_research(ident)==view
    assert len(service.list_runs()['runs'])==4


@pytest.mark.parametrize('busy',[False,True])
def test_training_cancel_during_request_creation_forwards_only_owned_request(tmp_path,monkeypatch,busy):
    service,source,_=setup(tmp_path)
    ident=service.create_research(source,'purpose',.5)['session']['id']
    original=service.create_ai_request
    def create(*args,**kwargs):
        service.cancel_research(ident,'training')
        return original(*args,**kwargs)
    monkeypatch.setattr(service,'create_ai_request',create)
    if busy:
        def unavailable(*args):raise ValueError('engine busy')
        monkeypatch.setattr(service,'cancel_ai_request',unavailable)
    view=service.prepare_research_training(ident)
    assert view['training']['aiRequestStatus']==('awaiting_candidates' if busy else 'cancelled')
    assert view['training']['cancellationRequested'] is True
    assert view['cancellation']['forwarded'] is not busy
    assert view['eligibility']['canFreeze'] is False
    assert view['training']['bindingStatus']=='not_bound'
    assert len(service.list_runs()['runs'])==1


def test_training_cancel_request_creation_collision_does_not_cancel_foreign_request(tmp_path,monkeypatch):
    service,source,_=setup(tmp_path)
    ident=service.create_research(source,'purpose',.5)['session']['id']
    original=service.create_ai_request
    foreign=[]
    def create(run_id,purpose,**kwargs):
        service.cancel_research(ident,'training')
        foreign.append(original(run_id,'different owner purpose',**kwargs))
        return original(run_id,purpose,**kwargs)
    monkeypatch.setattr(service,'create_ai_request',create)
    monkeypatch.setattr(service,'cancel_ai_request',lambda *_:pytest.fail('must not cancel foreign request'))
    view=service.prepare_research_training(ident)
    assert view['training']['bindingStatus']=='training_binding_failed'
    assert view['training']['aiRequestStatus'] is None
    assert service.get_ai_request(view['training']['aiRequestId'])['status']=='awaiting_candidates'
    assert view['training']['cancellationRequested'] is True
    assert len(service.list_runs()['runs'])==1
