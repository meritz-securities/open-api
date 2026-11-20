"""R-09: 로컬 데이터 초기화(reset_local_data) 와 저장 위치·암호화 고지(capabilities). 네트워크·실주문 없음."""
import re
import threading

import pytest

from runtime import RELEASE, VERSION
from runtime.service import StrategyRuntime
from runtime.storage import Store


def _populated(path):
    service = StrategyRuntime(path, catalog=None)
    revision = service.save_strategy(service.templates()['templates'][0])
    dataset = service.demo_data()
    run = service.run(revision['id'], dataset['id'])
    assert run['status'] == 'succeeded'
    service.create_research({'runId': run['id']}, '초기화 대상', .5)
    assert service.list_strategies()['strategies'] and service.list_data()['datasets'] and service.list_runs()['runs']
    return service, revision, dataset, run


def test_reset_clears_every_listing_and_store_keeps_working(tmp_path):
    service, revision, dataset, run = _populated(tmp_path)
    result = service.dispatch('reset_local_data', {'confirm': True})
    assert result['cleared'] is True and result['removedObjects'] >= 4
    assert result['storagePath'] == str(service.store.path) and result['storageEncrypted'] is False
    assert service.list_strategies() == {'strategies': []}
    assert service.list_data() == {'datasets': []}
    assert service.list_runs() == {'runs': [], 'validationJobs': []}
    assert service.list_research()['sessions'] == []
    assert service.list_ai_requests()['requests'] == []
    with pytest.raises(ValueError, match='Snapshot not found'):
        service.get_run(run['id'])
    # 재기동한 다른 인스턴스도 비어 있고, 같은 저장소에 다시 저장할 수 있다.
    reopened = StrategyRuntime(tmp_path, catalog=None)
    assert reopened.list_strategies()['strategies'] == []
    assert reopened.save_strategy(service.templates()['templates'][0])['id']
    assert service.dispatch('reset_local_data', {'confirm': True})['removedObjects'] == 1


@pytest.mark.parametrize('arguments', [{}, {'confirm': False}, {'confirm': 'true'}, {'confirm': 1}, {'confirm': None}])
def test_reset_requires_explicit_boolean_confirm(tmp_path, arguments):
    service, *_ = _populated(tmp_path)
    before = (service.list_strategies(), service.list_data(), service.list_runs())
    with pytest.raises(ValueError, match='confirm'):
        service.dispatch('reset_local_data', arguments)
    assert (service.list_strategies(), service.list_data(), service.list_runs()) == before


def test_reset_refused_while_job_active_in_process(tmp_path):
    service, *_ = _populated(tmp_path)
    with service._mutex:
        service._active['fixture-job'] = threading.Event()
    try:
        with pytest.raises(ValueError, match='실행 중인 작업'):
            service.reset_local_data(confirm=True)
    finally:
        with service._mutex:
            service._active.pop('fixture-job')
    assert service.list_runs()['runs']


def test_reset_refused_while_another_process_holds_engine_lock(tmp_path):
    service, *_ = _populated(tmp_path)
    other = Store(tmp_path)                 # 별도 프로세스를 흉내: 같은 저장소의 다른 잠금 보유자
    with other.execution_lock():
        with pytest.raises(ValueError, match='Another local run is active'):
            service.store.clear()
        with pytest.raises(ValueError, match='다른 로컬 프로세스'):
            service.reset_local_data(confirm=True)
    assert service.list_runs()['runs'] and service.list_strategies()['strategies']
    assert service.reset_local_data(confirm=True)['cleared'] is True


def test_reset_refused_while_ai_session_busy(tmp_path, monkeypatch):
    service, *_ = _populated(tmp_path)
    monkeypatch.setattr(service._ai_studio, 'get_ai_connection', lambda: {'busy': True})
    with pytest.raises(ValueError, match='AI 생성'):
        service.reset_local_data(confirm=True)
    assert service.list_runs()['runs']


def test_store_clear_counts_and_vacuums(tmp_path):
    store = Store(tmp_path)
    for index in range(5):
        store.put('revision', f'r{index}', {'id': f'r{index}', 'definition': {'bulk': 'x' * 50_000}})
    size_before = store.path.stat().st_size
    assert store.clear() == 5
    assert store.list('revision') == [] and store.clear() == 0
    assert store.path.stat().st_size <= size_before
    store.put('revision', 'again', {'id': 'again'})
    assert store.get('revision', 'again') == {'id': 'again'}


def test_capabilities_disclose_storage_path_and_release(tmp_path):
    service = StrategyRuntime(tmp_path, catalog=None)
    caps = service.capabilities()
    assert caps['storagePath'] == str(tmp_path / 'strategy.sqlite3') == str(service.store.path)
    assert caps['storageEncrypted'] is False and caps['storage'] == 'local SQLite, not encrypted'
    assert caps['version'] == VERSION and caps['release'] == RELEASE
    assert re.fullmatch(r'\d+\.\d+\.\d+(-(rc|beta)\.?\d+)?', VERSION) and 'not a general release' in RELEASE
    assert 'reset_local_data' not in caps          # 액션은 dispatch 로만 노출
