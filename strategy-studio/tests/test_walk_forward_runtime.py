"""Real shared-engine walk-forward selection, lifecycle, persistence and MCP."""
import asyncio
import datetime as dt

import pytest
from fastmcp import Client, FastMCP
from runtime.service import StrategyRuntime
from runtime.mcp import register_platform


def setup(root):
    service = StrategyRuntime(root)
    definition = {'schemaVersion':1,'kind':'dsl','name':'buy hold','entry':{'op':'gt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},'exit':{'op':'lt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},'quantity':1,'allowPyramiding':False}
    baseline = service.save_strategy(definition)
    alternative = service.save_strategy({**definition, 'name':'two shares', 'quantity':2})
    bars = [{'date':str(dt.date(2025,1,1)+dt.timedelta(days=i)), 'open':p,'high':p,'low':p,'close':p} for i,p in enumerate([10,20,30,40,50,60,70,80])]
    dataset = service.import_data(bars, {'kind':'imported','label':'independent walk-forward oracle'})
    run = service.run(baseline['id'],dataset['id'],{'initialCash':1000,'feeBps':0,'taxBps':0,'slippageBps':0})
    return service, run, alternative, {'candidateRevisionIds':[alternative['id']], 'trainBars':4, 'testBars':2}


def test_real_training_selection_partition_reuse_and_persistence(tmp_path):
    service, baseline, alternative, options = setup(tmp_path)
    result = service.validate_robustness(baseline['id'], 'walk_forward', options)
    assert result['result']['holdoutContaminated'] is True
    assert 'trialCount' not in result['result']
    assert result['result']['fullSnapshotRunCount'] == 1
    assert result['result']['candidateCount'] == 2
    assert result['result']['foldCount'] == 2
    assert result['result']['childRunCount'] == 6
    folds = result['result']['folds']
    assert len(folds) == 2
    for fold in folds:
        assert fold['selectedRevisionId'] == alternative['id']
        assert [x['result']['metrics']['totalReturnPct'] for x in fold['training']] == pytest.approx([2,4])
        assert fold['test']['result']['metrics']['totalReturnPct'] == 0
        assert fold['train']['endDate'] < fold['testWindow']['startDate']
        train_runs = [service.get_run(x['result']['runId']) for x in fold['training']]
        assert train_runs[0]['datasetId'] == train_runs[1]['datasetId']
        assert all(r['parentRunId'] == baseline['id'] for r in train_runs)
        assert all(r['validationJobId'] == result['jobId'] for r in train_runs)
        assert service.get_run(fold['test']['result']['runId'])['revisionId'] == alternative['id']
    assert len(service.store.get('validation-job', result['jobId'])['childRunIds']) == 6
    assert len(service.list_data()['datasets']) == 1
    partitions=[d for d in service.store.list('dataset') if d.get('derivedFrom')]
    assert len(partitions) == 4
    assert all(d['derivedFrom']==baseline['datasetId'] and d['validationJobId']==result['jobId'] for d in partitions)
    assert StrategyRuntime(tmp_path).dispatch('get_validation',{'validationId':result['id']}) == result
    assert service.get_run(baseline['id']) == baseline


@pytest.mark.parametrize('changes', [{'trainBars':True}, {'trainBars':1}, {'testBars':0}, {'extra':1}, {'candidateRevisionIds':['missing']}, {'trainBars':7,'testBars':2}])
def test_bad_options_do_not_execute_children(tmp_path, changes):
    service, baseline, _, options = setup(tmp_path)
    with pytest.raises(ValueError): service.validate_robustness(baseline['id'],'walk_forward',{**options,**changes})
    assert len(service.list_runs()['runs']) == 1
    assert service.list_runs()['validationJobs'] == []
    assert len(service.list_data()['datasets']) == 1


def test_deadline_interrupts_engine_and_records_failed_job_and_child(tmp_path, monkeypatch):
    import runtime.service as module
    service, baseline, _, options = setup(tmp_path)
    monkeypatch.setattr(module, 'WALK_FORWARD_TIMEOUT_SECONDS', .03)
    def wait_for_cancel(definition, data, settings, cancel):
        assert cancel.wait(2)
        raise ValueError('cancelled')
    monkeypatch.setattr(service,'_calculate',wait_for_cancel)
    with pytest.raises(ValueError,match='전체 시간'):
        service.validate_robustness(baseline['id'],'walk_forward',options)
    job = service.list_runs()['validationJobs'][0]
    assert job['status'] == 'failed'
    record = service.store.get('validation-job',job['id'])
    assert len(record['childRunIds']) == 1
    assert service.get_run(record['childRunIds'][0])['status'] == 'cancelled'
    assert not service.store.list('validation')
    assert service._active == {}
    with service.store.execution_lock(): pass


def test_mcp_runs_and_retrieves_same_stored_validation(tmp_path):
    service, baseline, _, options = setup(tmp_path)
    async def check():
        mcp = FastMCP('walk-forward')
        register_platform(mcp,None,None,service)
        async with Client(mcp) as client:
            record=(await client.call_tool('meritz_strategy_robustness',{'runId':baseline['id'],'method':'walk_forward','options':options})).data
            restored=(await client.call_tool('meritz_strategy_validation_result',{'validationId':record['id']})).data
            assert restored == record
    asyncio.run(check())


def test_real_engine_test_ranking_does_not_change_training_winner(tmp_path):
    service, baseline, alternative, options = setup(tmp_path)
    prices = [10,20,30,40,80,70,50]
    bars = [{'date':str(dt.date(2025,2,1)+dt.timedelta(days=i)), 'open':p,'high':p,'low':p,'close':p} for i,p in enumerate(prices)]
    data=service.import_data(bars,{'kind':'imported','label':'inverted train/test rank'})
    original=service.run(baseline['revisionId'],data['id'],baseline['settings'])
    record=service.validate_robustness(original['id'],'walk_forward',{**options,'testBars':3})
    fold=record['result']['folds'][0]
    assert fold['selectedRevisionId']==alternative['id']
    assert [x['result']['metrics']['totalReturnPct'] for x in fold['training']]==pytest.approx([2,4])
    assert fold['test']['result']['metrics']['totalReturnPct']==pytest.approx(-4)
    test_run=service.get_run(fold['test']['result']['runId'])
    lower_loss=service.run(baseline['revisionId'],test_run['datasetId'],baseline['settings'])
    assert lower_loss['report']['metrics']['totalReturnPct']==pytest.approx(-2)
    assert service.get_validation(record['id'])['result']['folds'][0]['selectedRevisionId']==alternative['id']


def test_real_engine_observes_deadline_between_decisions(tmp_path, monkeypatch):
    import time
    import runtime.service as module
    import runtime.dsl as dsl
    service, baseline, _, options = setup(tmp_path)
    original=dsl.make_decider
    calls=[]
    def slow_decider(*args, **kwargs):
        decide=original(*args, **kwargs)
        def callback(ctx):
            calls.append(ctx['index'])
            time.sleep(.04)
            return decide(ctx)
        return callback
    monkeypatch.setattr(dsl,'make_decider',slow_decider)
    monkeypatch.setattr(module,'WALK_FORWARD_TIMEOUT_SECONDS',.02)
    with pytest.raises(ValueError,match='전체 시간'):
        service.validate_robustness(baseline['id'],'walk_forward',options)
    assert len(calls) <= 1
    job=service.list_runs()['validationJobs'][0]
    child=service.store.get('validation-job',job['id'])['childRunIds'][0]
    assert service.get_run(child)['status']=='cancelled'
    assert service.list_runs()['validationJobs'][0]['status']=='failed'


def test_candidate_hash_tampering_rejected_before_job(tmp_path, monkeypatch):
    import copy
    service, baseline, alternative, options = setup(tmp_path)
    original=service.store.get
    def get(kind, ident):
        value=original(kind,ident)
        if kind=='revision' and ident==alternative['id']:
            value=copy.deepcopy(value);value['definition']['quantity']=999
        return value
    monkeypatch.setattr(service.store,'get',get)
    with pytest.raises(ValueError,match='무결성'):
        service.validate_robustness(baseline['id'],'walk_forward',options)
    assert not service.list_runs()['validationJobs']


def test_python_window_cap_and_seven_folds_rejected_before_job(tmp_path):
    service, baseline, _, options = setup(tmp_path)
    python=service.save_strategy({'schemaVersion':1,'name':'hold','kind':'python','code':'def on_bar(ctx): return {"action":"hold"}'})
    bars=[{'date':str(dt.date(2020,1,1)+dt.timedelta(days=i)), 'open':10,'high':10,'low':10,'close':10} for i in range(1004)]
    data=service.import_data(bars,{'kind':'imported','label':'window-boundary'})
    run=service.run(baseline['revisionId'],data['id'])
    for settings,message in [({'trainBars':1001,'testBars':3,'candidateRevisionIds':[python['id']]},'1000'),({'trainBars':990,'testBars':2},'1~6')]:
        with pytest.raises(ValueError,match=message): service.validate_robustness(run['id'],'walk_forward',settings)
    assert not service.list_runs()['validationJobs']
    assert len(service.list_data()['datasets'])==2


def test_interrupted_validation_child_retains_job_link(tmp_path):
    from runtime.storage import Store
    store=Store(tmp_path)
    store.put('validation-job','job',{'id':'job','status':'running','method':'walk_forward','childRunIds':[]})
    store.put('run','child',{'id':'child','status':'running','validationJobId':'job','parentRunId':'baseline'})
    reopened=Store(tmp_path)
    assert reopened.get('validation-job','job')['status']=='interrupted'
    child=reopened.get('run','child')
    assert child['status']=='interrupted' and child['validationJobId']=='job'
    assert reopened.list_summaries('run')[0]['validationJobId']=='job'
