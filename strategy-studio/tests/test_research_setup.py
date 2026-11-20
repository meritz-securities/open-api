"""Research setup is storage-only and binds exact chronological source snapshots."""
import copy
import math
import pytest
from runtime.service import StrategyRuntime
from runtime.storage import digest, encode_body
from optimizer import chronological_split


def setup(path, prices=(10,20,30,40,50,60)):
    runtime=StrategyRuntime(path)
    definition={'schemaVersion':1,'kind':'dsl','name':'research fixture','quantity':1,
                'entry':{'op':'gt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}},
                'exit':{'op':'lt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}}}
    revision=runtime.save_strategy(definition)
    bars=[{'date':f'2025-01-{i+1:02d}','open':p,'high':p,'low':p,'close':p} for i,p in enumerate(prices)]
    data=runtime.import_data(bars,{'kind':'imported','label':'synthetic research oracle','currency':'KRW'})
    source={'revisionId':revision['id'],'datasetId':data['id'],
            'settings':{'initialCash':1000,'feeBps':0,'slippageBps':0,'taxBps':0}}
    return runtime,source,bars


def test_create_fresh_splits_without_engine_or_holdout_dataset(tmp_path,monkeypatch):
    runtime,source,bars=setup(tmp_path)
    monkeypatch.setattr(runtime,'_calculate',lambda *a,**k:pytest.fail('creation must not execute engine'))
    view=runtime.create_research(source,'검증 구간은 후보 확정 후 확인',.5)
    plan=view['session'];train=runtime._dataset(plan['split']['train']['datasetId'])
    assert train['bars']==bars[:3]
    assert plan['split']['holdout']['barsHash']==digest(bars[3:])
    assert plan['split']['holdout']['datasetId'] is None
    assert len(runtime.store.list('dataset'))==2 and not runtime.list_runs()['runs']
    assert not runtime.store.list('validation-job') and not runtime.store.list('ai-request')
    assert plan['split']['pivotIndex']==3
    assert plan['split']['train']['endDate']<'2025-01-04'==plan['split']['holdout']['startDate']
    assert plan['disclosures']['fullPeriodObserved'] is False
    assert plan['disclosures']['priorFullDatasetRunCount']==0
    assert plan['disclosures']['strictBlindEvaluation'] is False
    assert plan['disclosures']['humanExposureUnknown'] is True
    assert 'bars' not in plan['split']['train'] and 'bars' not in plan['split']['holdout']
    prefix,suffix=chronological_split(bars,.5)
    assert prefix+suffix==bars and not set(b['date'] for b in prefix)&set(b['date'] for b in suffix)


def test_completed_source_disclosure_and_idempotency(tmp_path):
    runtime,source,_=setup(tmp_path)
    baseline=runtime.run(**source)
    ident='a'*32
    first=runtime.create_research({'runId':baseline['id']},'원본 개선',.5,ident)
    assert first['session']['disclosures']['sourceWasCompletedRun'] is True
    assert first['session']['disclosures']['priorFullDatasetRunCount']==1
    assert runtime.create_research({'runId':baseline['id']},'원본 개선',.5,ident)==first
    assert len(runtime.store.list('dataset'))==2 and len(runtime.list_runs()['runs'])==1
    with pytest.raises(ValueError,match='different request'):
        runtime.create_research({'runId':baseline['id']},'다른 목적',.5,ident)
    assert runtime.get_run(baseline['id'])==baseline
    assert StrategyRuntime(tmp_path).get_research(ident)==first


def test_fresh_source_counters_include_prior_runs_and_research(tmp_path):
    runtime,source,_=setup(tmp_path)
    first=runtime.create_research(source,'first',.5)
    runtime.run(**source)
    second=runtime.create_research(source,'second',.5)
    disclosures=second['session']['disclosures']
    assert disclosures['sourceWasCompletedRun'] is False
    assert disclosures['fullPeriodObserved'] is True
    assert disclosures['priorResearchSessionCount']==1
    assert disclosures['priorFullDatasetRunCount']==1
    assert len(runtime.list_research(source={'datasetId':source['datasetId']})['sessions'])==2
    assert runtime.list_research(limit=1)['sessions'][0]['id']==second['session']['id']
    assert first['session']['disclosures']['priorResearchSessionCount']==0


@pytest.mark.parametrize('patch',[
    {'runId':'missing'}, {'revisionId':'missing','datasetId':'missing'},
    {'runId':'missing','datasetId':'also'}, {'revisionId':'only'}, {'datasetId':'only'},
    {'revisionId':'x','datasetId':'y','extra':1}, [], None,
])
def test_invalid_sources_do_not_create_records(tmp_path,patch):
    runtime,_,_=setup(tmp_path)
    with pytest.raises(ValueError):runtime.create_research(patch,'purpose')
    assert not runtime.store.list('research-plan') and len(runtime.store.list('dataset'))==1


@pytest.mark.parametrize('fraction',[True,0,.19,.81,math.inf,math.nan,'0.5'])
def test_invalid_fraction_rejected_before_writes(tmp_path,fraction):
    runtime,source,_=setup(tmp_path)
    with pytest.raises(ValueError):runtime.create_research(source,'purpose',fraction)
    assert len(runtime.store.list('dataset'))==1


def test_short_partition_and_python_source_rejected(tmp_path):
    runtime,source,_=setup(tmp_path,prices=(10,20,30,40))
    with pytest.raises(ValueError):runtime.create_research(source,'purpose',.2)
    revision=runtime.save_strategy({'schemaVersion':1,'kind':'python','name':'unsupported research source','code':'def on_bar(ctx): return {"action":"hold"}'})
    with pytest.raises(ValueError,match='DSL'):runtime.create_research({**source,'revisionId':revision['id']},'purpose',.5)
    assert not runtime.store.list('research-plan')


def test_get_is_read_only_even_with_stale_running_job(tmp_path):
    runtime,source,_=setup(tmp_path)
    view=runtime.create_research(source,'read only',.5)
    plan=view['session'];runtime.store.put('validation-job',plan['trainingJobId'],{'id':plan['trainingJobId'],'status':'running','method':'research_training'})
    def rows():
        with runtime.store.connection() as con:return con.execute('SELECT kind,id,body FROM objects ORDER BY kind,id').fetchall()
    before=rows()
    runtime.dispatch('get_research',{'researchId':plan['id']})
    assert rows()==before
    assert runtime.store.get('validation-job',plan['trainingJobId'])['status']=='running'


@pytest.mark.parametrize('target',['revision','dataset','train','plan'])
def test_source_partition_and_plan_tampering_rejected(tmp_path,target):
    runtime,source,_=setup(tmp_path);plan=runtime.create_research(source,'integrity',.5)['session']
    kind,ident=('revision',source['revisionId']) if target=='revision' else ('dataset',source['datasetId']) if target=='dataset' else ('dataset',plan['split']['train']['datasetId']) if target=='train' else ('research-plan',plan['id'])
    value=runtime.store.get(kind,ident)
    if target=='revision':value['definition']['quantity']=2;value['hash']=digest(value['definition'])
    elif target in ('dataset','train'):
        value['warnings']=['changed'];value['qualityHash']=digest(value['warnings'])
    else:value['purpose']='changed'
    with runtime.store.connection() as con:con.execute('UPDATE objects SET body=? WHERE kind=? AND id=?',(encode_body(value),kind,ident))
    with pytest.raises(ValueError):runtime.get_research(plan['id'])


def test_training_hash_independent_of_suffix_but_suffix_binding_changes(tmp_path):
    runtime,source,bars=setup(tmp_path)
    first=runtime.create_research(source,'same purpose',.5)['session']
    changed=copy.deepcopy(bars)
    for bar in changed[3:]:
        for field in ('open','high','low','close'):bar[field]=999999
    data=runtime.import_data(changed,{'kind':'imported','label':'synthetic research oracle','currency':'KRW'})
    second=runtime.create_research({**source,'datasetId':data['id']},'same purpose',.5)['session']
    assert first['split']['train']['expectedContentHash']==second['split']['train']['expectedContentHash']
    assert first['split']['holdout']['barsHash']!=second['split']['holdout']['barsHash']


@pytest.mark.parametrize('kwargs',[{'limit':True},{'limit':101},{'limit':0},{'source':{'revisionId':'x'}},{'source':{'runId':'x','datasetId':'y'}},{'source':{'unknown':'x'}}])
def test_listing_bounds(tmp_path,kwargs):
    runtime,_,_=setup(tmp_path)
    with pytest.raises(ValueError):runtime.list_research(**kwargs)


@pytest.mark.parametrize('missing', ['plan', 'revision', 'source_data', 'training_data'])
def test_only_missing_plan_has_distinct_read_only_recovery_error(tmp_path, missing):
    runtime,source,_=setup(tmp_path)
    plan=runtime.create_research(source,'purpose',.5)['session']
    target={
        'plan':('research-plan',plan['id']),
        'revision':('revision',source['revisionId']),
        'source_data':('dataset',source['datasetId']),
        'training_data':('dataset',plan['split']['train']['datasetId']),
    }[missing]
    with runtime.store.connection() as con:
        deleted=con.execute('DELETE FROM objects WHERE kind=? AND id=?', target)
        assert deleted.rowcount==1
        before=con.execute('SELECT * FROM objects ORDER BY kind,id').fetchall()
    with pytest.raises(ValueError) as error:
        runtime.get_research(plan['id'])
    assert str(error.value)==('RESEARCH_NOT_FOUND' if missing=='plan' else 'Snapshot not found')
    with runtime.store.connection() as con:
        assert con.execute('SELECT * FROM objects ORDER BY kind,id').fetchall()==before
