"""Independent arithmetic and bounded parameter selection on the shared engine."""
import copy
import math
import pytest
from optimizer.parameter_grid import plan_grid
from runtime.service import StrategyRuntime


def definition():
    return {'schemaVersion':1,'kind':'dsl','name':'grid oracle',
            'entry':{'op':'gt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},
            'exit':{'op':'lt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},'quantity':1}


def setup(path):
    runtime=StrategyRuntime(path)
    revision=runtime.save_strategy(definition())
    bars=[{'date':f'2025-01-0{i+1}','open':p,'high':p,'low':p,'close':p} for i,p in enumerate([10,20,40])]
    data=runtime.import_data(bars,{'kind':'imported','label':'Synthetic grid arithmetic fixture'})
    baseline=runtime.run(revision['id'],data['id'],{'initialCash':100,'feeBps':0,'slippageBps':0,'taxBps':0})
    return runtime,baseline


def test_shared_engine_oracle_original_preserved_and_replay(tmp_path):
    runtime,baseline=setup(tmp_path)
    options={'axes':[{'path':'quantity','values':[2,1]}],'maxEvaluations':2}
    saved=runtime.dispatch('validate_robustness',{'runId':baseline['id'],'method':'parameter_grid','options':options})
    result=saved['result']
    # Signal at 10, next-open fill at 20, final mark at 40: cash 60+80=140 or 80+40=120.
    assert [x['result']['metrics']['totalReturnPct'] for x in result['results']]==pytest.approx([40,20])
    assert result['baselineReturnPct']==pytest.approx(20)
    assert result['bestCandidateIndex']==1 and result['bestCandidateImproved'] is True
    for item in result['results']:
        run=runtime.get_run(item['result']['runId'])
        assert run['datasetId']==baseline['datasetId'] and run['settings']==baseline['settings']
        assert run['parentRunId']==baseline['id'] and run['validationJobId']==saved['jobId']
        repeat=runtime.run(run['revisionId'],run['datasetId'],run['settings'])
        assert repeat['manifest']['resultHash']==run['manifest']['resultHash']
    assert runtime.get_run(baseline['id'])==baseline
    assert StrategyRuntime(tmp_path).get_validation(saved['id'])==saved
    job=runtime.store.get('validation-job',saved['jobId'])
    assert len(job['childRunIds'])==2 and len(job['candidateResults'])==2
    assert len(runtime.get_validation_job(saved['jobId'])['candidateResults'])==2


@pytest.mark.parametrize('axis',[
 {'path':'quantity','values':[True]}, {'path':'quantity','values':[math.nan]},
 {'path':'quantity','values':[math.inf]}, {'path':'quantity','values':[1,1.0]},
 {'path':'quantity','values':[1.5]}, {'path':'entry.left.value','values':[2]},
 {'path':'entry.right.type','values':[2]}, {'path':'name','values':[2]},
 {'path':'entry.conditions.00.right.value','values':[2]},
 {'path':'risk.stopLossPct','values':[0]}, {'path':'quantity','values':[]},
 {'path':'quantity','values':[1],'extra':1},
])
def test_invalid_axes_no_side_effect(tmp_path,axis):
    runtime,baseline=setup(tmp_path)
    with pytest.raises(ValueError):
        runtime.validate_robustness(baseline['id'],'parameter_grid',{'axes':[axis]})
    assert len(runtime.list_runs()['runs'])==1
    assert len(runtime.list_strategies()['strategies'])==1
    assert not runtime.list_runs()['validationJobs']


def test_cartesian_order_limits_nested_operand_and_no_mutation():
    original=definition(); snapshot=copy.deepcopy(original)
    axes=[{'path':'quantity','values':[2,1]},{'path':'entry.right.value','values':[5,10]}]
    plans=plan_grid(original,axes,4)
    assert [list(p['parameterValues'].values()) for p in plans]==[[2,5],[2,10],[1,5],[1,10]]
    assert original==snapshot
    with pytest.raises(ValueError): plan_grid(original,axes,3)
    with pytest.raises(ValueError): plan_grid(original,axes,True)
    with pytest.raises(ValueError): plan_grid(original,[axes[0],axes[0]])
    with pytest.raises(ValueError): plan_grid(original,[{'path':'quantity','values':list(range(1,14))}])
    nested=definition();nested['entry']={'op':'all','conditions':[{'op':'gt','left':{'type':'sma','period':2},'right':{'type':'constant','value':0}}]}
    assert plan_grid(nested,[{'path':'entry.conditions.0.left.period','values':[2,250]}])[1]['definition']['entry']['conditions'][0]['left']['period']==250
    with pytest.raises(ValueError):plan_grid(nested,[{'path':'entry.conditions.0.left.period','values':[2.0]}])


def test_failures_retained_and_no_winner(tmp_path,monkeypatch):
    runtime,baseline=setup(tmp_path); original=runtime._calculate
    def calculate(d,*args,**kwargs):
        if d['quantity']==2: raise ValueError('private failure detail')
        return original(d,*args,**kwargs)
    monkeypatch.setattr(runtime,'_calculate',calculate)
    record=runtime.validate_robustness(baseline['id'],'parameter_grid',{'axes':[{'path':'quantity','values':[1,2,3]}]})
    result=record['result']
    assert [r['status'] for r in result['results']]==['succeeded','failed','succeeded']
    assert result['bestCandidateIndex'] is None and result['bestCandidateImproved'] is None
    assert result['status']=='completed_with_failures'
    failed=result['results'][1]
    assert runtime.get_run(failed['runId'])['revisionId']==failed['revisionId']
    assert 'private failure' not in str(record)


def test_deadline_records_completed_candidates_and_cancelled_child(tmp_path,monkeypatch):
    import runtime.service as module
    runtime,baseline=setup(tmp_path); original=runtime._calculate
    monkeypatch.setattr(module,'PARAMETER_GRID_TIMEOUT_SECONDS',.1)
    def calculate(d,data,settings,cancel):
        if d['quantity']==2:
            assert cancel.wait(2)
            raise ValueError('cancelled')
        return original(d,data,settings,cancel)
    monkeypatch.setattr(runtime,'_calculate',calculate)
    with pytest.raises(ValueError,match='전체 시간'):
        runtime.validate_robustness(baseline['id'],'parameter_grid',{'axes':[{'path':'quantity','values':[1,2,3]}]})
    job=runtime.store.get('validation-job',runtime.list_runs()['validationJobs'][0]['id'])
    assert job['status']=='failed' and len(job['childRunIds'])==2
    assert len(job['candidateResults'])==1 and len(job['candidatePlan'])==3
    assert runtime.get_run(job['childRunIds'][1])['status']=='cancelled'
    assert [item['status'] for item in runtime.get_validation_job(job['id'])['candidateResults']]==['succeeded','cancelled','not_run']
    assert runtime._active=={}
    with runtime.store.execution_lock(): pass


def test_candidate_tie_and_worse_than_original(tmp_path):
    runtime,baseline=setup(tmp_path)
    record=runtime.validate_robustness(baseline['id'],'parameter_grid',{'axes':[{'path':'entry.right.value','values':[100,200]}]})
    result=record['result']
    assert [r['result']['metrics']['totalReturnPct'] for r in result['results']]==[0,0]
    assert result['bestCandidateIndex']==1 and result['bestCandidateImproved'] is False


def test_interrupted_job_reconstructs_completed_and_inflight_children(tmp_path,monkeypatch):
    runtime,baseline=setup(tmp_path); original=runtime._calculate
    def calculate(d,*args,**kwargs):
        if d['quantity']==2: raise SystemExit('simulated process stop')
        return original(d,*args,**kwargs)
    monkeypatch.setattr(runtime,'_calculate',calculate)
    with pytest.raises(SystemExit):
        runtime.validate_robustness(baseline['id'],'parameter_grid',{'axes':[{'path':'quantity','values':[1,2,3]}]})
    reopened=StrategyRuntime(tmp_path)
    job_id=reopened.list_runs()['validationJobs'][0]['id']
    recovered=reopened.dispatch('get_validation_job',{'jobId':job_id})
    assert recovered['status']=='interrupted'
    assert [r['status'] for r in recovered['candidateResults']]==['succeeded','interrupted','not_run']
    assert recovered['candidateResults'][0]['result']['metrics']['totalReturnPct']==pytest.approx(20)
    assert len(recovered['childRunIds'])==2 and recovered['baselineReturnPct']==pytest.approx(20)
    assert reopened.get_run(baseline['id'])==baseline
    # Read-only recovery is stable; it does not rewrite finalized snapshots.
    assert reopened.get_validation_job(job_id)==recovered


@pytest.mark.parametrize('options',[{}, {'axes':[]}, {'axes':[{'path':'quantity','values':[10**1000]}]}])
def test_missing_or_extreme_parameters_are_value_errors(tmp_path,options):
    runtime,baseline=setup(tmp_path)
    with pytest.raises(ValueError):runtime.validate_robustness(baseline['id'],'parameter_grid',options)
    assert not runtime.list_runs()['validationJobs']


def test_failed_revision_before_child_is_preserved_in_retrieval(tmp_path,monkeypatch):
    runtime,baseline=setup(tmp_path); original=runtime.save_strategy
    def save(candidate,*args,**kwargs):
        if candidate['quantity']==2:raise ValueError('revision could not be saved')
        return original(candidate,*args,**kwargs)
    monkeypatch.setattr(runtime,'save_strategy',save)
    record=runtime.validate_robustness(baseline['id'],'parameter_grid',{'axes':[{'path':'quantity','values':[1,2,3]}]})
    job=runtime.get_validation_job(record['jobId'])
    assert [r['status'] for r in record['result']['results']]==['succeeded','failed','succeeded']
    assert [r['status'] for r in job['candidateResults']]==['succeeded','failed','succeeded']
    assert 'runId' not in job['candidateResults'][1]
    assert len(job['childRunIds'])==2
    assert StrategyRuntime(tmp_path).get_validation_job(record['jobId'])==job


@pytest.mark.parametrize('key',['engineVersion','runtimeVersion','dataHash'])
def test_baseline_provenance_mismatch_suppresses_improvement(tmp_path,monkeypatch,key):
    runtime,baseline=setup(tmp_path); original=runtime.get_run
    def read(ident):
        value=original(ident)
        if ident==baseline['id']:
            value=copy.deepcopy(value);value['manifest'][key]='older-or-other'
        return value
    monkeypatch.setattr(runtime,'get_run',read)
    record=runtime.validate_robustness(baseline['id'],'parameter_grid',{'axes':[{'path':'quantity','values':[1,2]}]})
    assert record['result']['bestCandidateIndex']==2
    assert record['result']['baselineComparable'] is False
    assert record['result']['bestCandidateImproved'] is None
    assert record['result']['comparisonWarning']
    assert original(baseline['id'])==baseline


def test_same_provenance_and_saved_failure_plan_validation(tmp_path,monkeypatch):
    runtime,baseline=setup(tmp_path)
    record=runtime.validate_robustness(baseline['id'],'parameter_grid',{'axes':[{'path':'quantity','values':[1,2]}]})
    assert record['result']['baselineComparable'] is True
    assert record['result']['bestCandidateImproved'] is True
    original=runtime.store.get
    def read(kind,ident):
        value=original(kind,ident)
        if kind=='validation-job':
            value=copy.deepcopy(value);value['candidateResults'][0]['parameterValues']['quantity']=99
        return value
    monkeypatch.setattr(runtime.store,'get',read)
    with pytest.raises(ValueError,match='plan'):runtime.get_validation_job(record['jobId'])
