"""Thread/process lifecycle tests, independent of UI fixtures."""
import subprocess
import sys
import threading
import pytest
from runtime.service import StrategyRuntime
from runtime.storage import Store


def baseline(tmp_path):
    service = StrategyRuntime(tmp_path)
    revision = service.save_strategy(service.templates()['templates'][0])
    data = service.demo_data()
    return service, service.run(revision['id'], data['id'])


@pytest.mark.parametrize('method', ['cost_sensitivity', 'out_of_sample', 'walk_forward'])
def test_validation_cancel_interrupts_current_and_remaining_candidates(tmp_path, monkeypatch, method):
    service, run = baseline(tmp_path)
    entered = threading.Event(); calls = []; errors = []
    def calculate(definition, data, settings, cancel):
        calls.append(cancel)
        entered.set()
        assert cancel.wait(3), 'same-process cancellation must reach the active engine'
        raise ValueError('cancelled inside engine')
    monkeypatch.setattr(service, '_calculate', calculate)
    def execute():
        try: service.validate_robustness(run['id'], method, {'trainBars':80,'testBars':20} if method == 'walk_forward' else None)
        except ValueError as error: errors.append(str(error))
    thread = threading.Thread(target=execute)
    thread.start()
    try:
        assert entered.wait(3)
        jobs = service.list_runs()['validationJobs']
        assert len(jobs) == 1 and jobs[0]['status'] == 'running'
        assert service.cancel(jobId=jobs[0]['id'])['cancellationRequested']
        thread.join(3)
        assert not thread.is_alive()
        assert len(calls) == 1, 'remaining partitions/cost candidates must not execute'
        assert errors == ['검증이 취소되었습니다.']
        assert service.list_runs()['validationJobs'][0]['status'] == 'cancelled'
        job=service.store.get('validation-job', jobs[0]['id'])
        assert len(job['childRunIds']) == 1
        assert service.get_run(job['childRunIds'][0])['validationJobId'] == job['id']
        assert service.store.list('validation') == []
        assert service._active == {}
        with pytest.raises(ValueError): service.cancel(jobId=jobs[0]['id'])
        with service.store.execution_lock(): pass
    finally:
        for event in calls: event.set()
        thread.join(3)


def test_validation_terminal_success_and_failure(tmp_path):
    service, run = baseline(tmp_path)
    result = service.validate_robustness(run['id'], 'cost_sensitivity')
    job = service.store.get('validation-job', result['jobId'])
    assert job['status'] == 'succeeded' and job['validationId'] == result['id']
    assert len(result['result']['results']) == 3
    with pytest.raises(ValueError):
        service.validate_robustness(run['id'], 'out_of_sample', {'fraction': 2})
    assert service.list_runs()['validationJobs'][0]['status'] == 'failed'
    assert service._active == {}
    assert service.get_run(run['id'])['manifest'] == run['manifest']


def test_startup_recovers_orphan_jobs_without_new_execution(tmp_path):
    store = Store(tmp_path)
    for kind in ('run', 'validation-job'):
        store.put(kind, kind, {'id': kind, 'status': 'running'})
    reopened = Store(tmp_path)
    for kind in ('run', 'validation-job'):
        recovered = reopened.get(kind, kind)
        assert recovered['status'] == 'interrupted'
        assert recovered['error'] == '앱이 종료되기 전에 실행이 완료되지 않았습니다.'


def test_startup_does_not_change_other_process_active_jobs(tmp_path):
    code = '''from runtime.storage import Store
import sys
store=Store(sys.argv[1])
with store.execution_lock():
 for kind in ('run','validation-job'):
  store.put(kind,kind,{'id':kind,'status':'running'})
 print('ready',flush=True)
 sys.stdin.readline()
'''
    process = subprocess.Popen([sys.executable, '-c', code, str(tmp_path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'ready'
        second = Store(tmp_path)
        for kind in ('run', 'validation-job'):
            assert second.get(kind, kind)['status'] == 'running'
    finally:
        process.communicate('\n', timeout=5)
    restarted = Store(tmp_path)
    for kind in ('run', 'validation-job'):
        recovered = restarted.get(kind, kind)
        assert recovered['status'] == 'interrupted'
        assert recovered['error'] == '앱이 종료되기 전에 실행이 완료되지 않았습니다.'


def test_v1_migration_builds_indexed_summaries_and_counts(tmp_path):
    import json
    import sqlite3
    root = tmp_path / 'legacy'; root.mkdir()
    with sqlite3.connect(root / 'strategy.sqlite3') as con:
        con.execute('CREATE TABLE objects(kind TEXT,id TEXT,body TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(kind,id))')
        con.execute('PRAGMA user_version=1')
        con.execute('INSERT INTO objects(kind,id,body) VALUES(?,?,?)', ('run', 'old', json.dumps({'id': 'old', 'datasetId': 'data', 'status': 'succeeded', 'report': {'large': 'x' * 1000000}})))
    store = Store(root)
    assert store.list_summaries('run') == [{'id': 'old', 'datasetId': 'data', 'status': 'succeeded'}]
    assert store.count_by_dataset('run', 'data') == 1
    assert len(store.get('run', 'old')['report']['large']) == 1000000
    with store.connection() as con:
        assert con.execute('PRAGMA user_version').fetchone()[0] == 3
        # Listing/recovery/counting must not read terminal bodies at all.
        con.execute("UPDATE objects SET body='invalid JSON sentinel' WHERE id='old'")
    reopened = Store(root)
    assert reopened.list_summaries('run')[0]['id'] == 'old'
    assert reopened.count_by_dataset('run', 'data') == 1


def test_monte_carlo_checks_cancel_between_iterations_without_changing_rng():
    from optimizer import monte_carlo
    report = {'equity': [{'equity': n} for n in (100, 110, 90, 120)]}
    expected = monte_carlo(report, seed=12, iterations=4)
    checkpoints = []
    actual = monte_carlo(report, seed=12, iterations=4, cancel=lambda: checkpoints.append(True))
    assert actual == expected and len(checkpoints) == 4
    class Stop(BaseException): pass
    def cancel(): raise Stop()
    with pytest.raises(Stop): monte_carlo(report, cancel=cancel)


def test_quality_hash_rejects_warning_tampering(tmp_path):
    import json
    service = StrategyRuntime(tmp_path)
    data = service.demo_data()
    with service.store.connection() as con:
        row = con.execute("SELECT body FROM objects WHERE kind='dataset' AND id=?", (data['id'],)).fetchone()
        body = json.loads(row[0]); body['warnings'] = ['tampered']
        con.execute("UPDATE objects SET body=? WHERE kind='dataset' AND id=?", (json.dumps(body), data['id']))
    with pytest.raises(ValueError, match='품질 경고'):
        service._dataset(data['id'])


def test_monte_carlo_job_is_cancellable_and_cleans_up(tmp_path, monkeypatch):
    import optimizer
    service, run = baseline(tmp_path)
    entered = threading.Event(); release = threading.Event(); failures = []
    def calculation(report, cancel=None, **options):
        entered.set()
        assert release.wait(3)
        cancel()
        pytest.fail('cancelled Monte Carlo must not finish')
    monkeypatch.setattr(optimizer, 'monte_carlo', calculation)
    def execute():
        try: service.validate_robustness(run['id'], 'monte_carlo')
        except ValueError as error: failures.append(str(error))
    thread = threading.Thread(target=execute); thread.start()
    try:
        assert entered.wait(3)
        job = service.list_runs()['validationJobs'][0]
        service.cancel(jobId=job['id']); release.set(); thread.join(3)
        assert not thread.is_alive() and failures == ['검증이 취소되었습니다.']
        assert service.store.get('validation-job', job['id'])['status'] == 'cancelled'
        assert service._active == {} and service.store.list('validation') == []
    finally:
        release.set(); thread.join(3)


def test_validation_candidates_persist_individually_with_small_summary(tmp_path, monkeypatch):
    import json
    service,run=baseline(tmp_path)
    for method in ('cost_sensitivity','out_of_sample'):
        result=service.validate_robustness(run['id'],method)
        assert len(json.dumps(result).encode())<20000
        for row in result['result']['results']:
            summary=row['result'];child=service.get_run(summary['runId'])
            assert child['parentRunId']==run['id']
            assert 'equity' not in summary and 'orders' not in summary
            assert child['report']['equity']
            assert summary['metrics']==child['report']['metrics']
    def fail(*args,**kwargs):raise ValueError('intentional candidate failure')
    monkeypatch.setattr(service,'_calculate',fail)
    result=service.validate_robustness(run['id'],'cost_sensitivity')
    for row in result['result']['results']:
        assert row['status']=='failed'
        assert service.get_run(row['runId'])['status']=='failed'
