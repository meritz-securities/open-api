"""Two-process SQLite decision ordering, with a pause after guard read."""
from contextlib import contextmanager
import multiprocessing
import pytest
from runtime.service import StrategyRuntime
from test_research_lifecycle import prepared, submit


def _worker(root, ident, action, ready, start, begin, inside, release, pause, output):
    service=StrategyRuntime(root)
    original=service.store.connection
    paused=False
    @contextmanager
    def connection():
        nonlocal paused
        with original() as con:
            class Proxy:
                active=False
                def execute(self, sql, parameters=()):
                    nonlocal paused
                    if sql=='BEGIN IMMEDIATE':
                        begin.set()
                        result=con.execute(sql,parameters);self.active=True
                        return result
                    result=con.execute(sql,parameters)
                    if pause and self.active and not paused and sql=='SELECT 1 FROM objects WHERE kind=? AND id=?':
                        paused=True;inside.set()
                        if not release.wait(15):raise RuntimeError('test transaction barrier timeout')
                    return result
            yield Proxy()
    service.store.connection=connection
    ready.set()
    if not start.wait(15):raise RuntimeError('test start barrier timeout')
    try:
        if action=='freeze':value=service.freeze_research(ident)
        elif action=='evaluate':value=service.evaluate_research(ident)
        else:value=service.cancel_research(ident,action.removeprefix('cancel_'))
        output.put({'action':action,'ok':True,'cancellation':value.get('cancellation'),'status':value['evaluation']['status']})
    except ValueError as error:
        output.put({'action':action,'ok':False,'error':str(error)})


def race(root,ident,first_action,second_action):
    ctx=multiprocessing.get_context('spawn')
    output=ctx.Queue();events=[];processes=[]
    for action,pause in [(first_action,True),(second_action,False)]:
        ready,start,begin,inside,release=[ctx.Event() for _ in range(5)]
        processes.append(ctx.Process(target=_worker,args=(str(root),ident,action,ready,start,begin,inside,release,pause,output)))
        events.append((ready,start,begin,inside,release))
    try:
        for process in processes:process.start()
        assert all(group[0].wait(15) for group in events)
        events[0][1].set()
        assert events[0][3].wait(15) # first transaction has read its guard but not inserted
        events[1][1].set()
        assert events[1][2].wait(15) # second process is trying BEGIN IMMEDIATE
        events[0][4].set()
        results=[output.get(timeout=20),output.get(timeout=20)]
        for process in processes:
            process.join(10);assert process.exitcode==0
        return {result['action']:result for result in results}
    finally:
        for group in events:group[1].set();group[4].set()
        for process in processes:
            if process.is_alive():process.terminate()
            process.join(5)
        output.close()


@pytest.mark.parametrize('first',['freeze','cancel_training'])
def test_freeze_and_training_cancel_first_committed_decision_wins(tmp_path,first):
    service,_,ident,_=prepared(tmp_path);submit(service,ident)
    second='cancel_training' if first=='freeze' else 'freeze'
    results=race(tmp_path,ident,first,second)
    reopened=StrategyRuntime(tmp_path);view=reopened.get_research(ident)
    if first=='freeze':
        assert results['freeze']['ok'] is True
        assert results['cancel_training']['cancellation']['effective'] is False
        assert results['cancel_training']['cancellation']['reason']=='ALREADY_FROZEN'
        assert view['frozen'] is not None and view['training']['cancellationRequested'] is False
    else:
        assert results['cancel_training']['cancellation']['effective'] is True
        assert results['freeze']=={'action':'freeze','ok':False,'error':'TRAINING_CANCELLED'}
        assert view['frozen'] is None and view['eligibility']['canFreeze'] is False
    assert len(reopened.list_runs()['runs'])==3
    assert len(reopened.store.list('dataset'))==2


def test_evaluation_cancel_first_prevents_claim_and_materialization(tmp_path):
    service,_,ident,_=prepared(tmp_path);submit(service,ident);service.freeze_research(ident)
    results=race(tmp_path,ident,'cancel_evaluation','evaluate')
    assert results['cancel_evaluation']['cancellation']['effective'] is True
    assert results['evaluate']=={'action':'evaluate','ok':False,'error':'RESEARCH_CANCELLED'}
    reopened=StrategyRuntime(tmp_path);view=reopened.get_research(ident)
    assert view['evaluation']['jobStatus'] is None
    assert view['session']['split']['holdout']['datasetId'] is None
    assert len(reopened.store.list('dataset'))==2


def test_evaluation_claim_first_never_duplicates_slots_on_repeat(tmp_path):
    service,_,ident,_=prepared(tmp_path);submit(service,ident);service.freeze_research(ident)
    results=race(tmp_path,ident,'evaluate','cancel_evaluation')
    assert results['evaluate']['ok'] is True
    reopened=StrategyRuntime(tmp_path);view=reopened.get_research(ident)
    assert view['evaluation']['jobStatus'] in ('succeeded','cancelled','failed')
    count=len(reopened.list_runs()['runs'])
    assert 3 <= count <= 5
    reopened.evaluate_research(ident)
    assert len(reopened.list_runs()['runs'])==count
    assert len({row['runId'] for row in view['evaluation']['slots']})==2
