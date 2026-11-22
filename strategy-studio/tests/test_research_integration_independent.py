"""Independent hand-accounted research oracles; synthetic data, no model/network.

Expected values come from cash=100-20*q and final=cash+last_close*q.
This suite deliberately never uses the engine to construct expected values.
"""
import copy
import json
import socket
import sys

import pytest

from runtime import ai_generation
from runtime.ai_cli_provider import ProviderConfig
from runtime.ai_generation_binding import GenerationBinding
from runtime.service import StrategyRuntime

DATES = ['2025-01-02', '2025-01-03', '2025-01-06',
         '2025-01-07', '2025-01-08', '2025-01-09']
SETTINGS = {'initialCash': 100, 'feeBps': 0, 'taxBps': 0, 'slippageBps': 0}


def strategy(quantity):
    return {'schemaVersion': 1, 'kind': 'dsl', 'name': f'Fixed quantity {quantity}',
            'entry': {'op': 'gt', 'left': {'type': 'field', 'field': 'close'},
                      'right': {'type': 'constant', 'value': 0}},
            'exit': {'op': 'lt', 'left': {'type': 'field', 'field': 'close'},
                     'right': {'type': 'constant', 'value': 0}},
            'quantity': quantity, 'allowPyramiding': False,
            'risk': {'stopLossPct': None, 'takeProfitPct': None}}


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect',
                        lambda *args, **kwargs: pytest.fail('Network prohibited in synthetic research QA'))


def setup_research(root, *, retrospective=False, tail=(30, 20, 10)):
    # Config validation only. The executable is never invoked by preview.
    runtime = StrategyRuntime(root, ai_provider=ProviderConfig(sys.executable, 'GENERATED_TEST_KEY'))
    revision = runtime.save_strategy(strategy(1))
    bars = [{'date': date, 'open': price, 'high': price, 'low': price, 'close': price}
            for date, price in zip(DATES, (10, 20, 30, *tail))]
    dataset = runtime.import_data(bars, {'kind': 'imported', 'label': 'PRIVATE_FULL_HISTORY_LABEL'})
    source = {'revisionId': revision['id'], 'datasetId': dataset['id'], 'settings': SETTINGS}
    original = None
    if retrospective:
        original = runtime.run(revision['id'], dataset['id'], SETTINGS)
        source = {'runId': original['id']}
    created = runtime.create_research(source, 'Compare fixed position sizes', trainFraction=.5)
    return runtime, revision, dataset, source, original, created


def runs(runtime):
    return {row['id']: runtime.get_run(row['id']) for row in runtime.list_runs()['runs']}


@pytest.mark.parametrize('retrospective', [False, True])
def test_hand_accounted_frozen_selection_and_persistent_no_retry(tmp_path, retrospective):
    runtime, revision, dataset, source, original, created = setup_research(
        tmp_path / 'store', retrospective=retrospective)
    ident = created['session']['id']
    initial_count = int(retrospective)
    assert len(runs(runtime)) == initial_count
    assert created['session']['split']['pivotIndex'] == 3
    assert created['session']['split']['holdout']['datasetId'] is None
    assert created['evaluation']['datasetId'] is None
    assert created['session']['disclosures']['strictBlindEvaluation'] is False
    assert created['session']['disclosures']['sourceWasCompletedRun'] is retrospective
    ready = runtime.prepare_research_training(ident)
    train_id = ready['training']['baselineRunId']
    baseline = runtime.get_run(train_id)
    assert baseline['report']['metrics']['finalEquity'] == 110
    assert baseline['report']['metrics']['totalReturnPct'] == pytest.approx(10)
    assert baseline['report']['trades'][0]['date'] == DATES[1]
    assert len(runs(runtime)) == initial_count + 1
    request_id = ready['training']['aiRequestId']
    assert runtime.get_ai_request(request_id)['request']['runId'] == train_id
    submitted = runtime.submit_ai_candidates(request_id, [strategy(2), strategy(3)])
    assert [a['status'] for a in submitted['attempts']] == ['succeeded', 'succeeded']
    candidate_runs = [runtime.get_run(a['runId']) for a in submitted['attempts']]
    assert [r['report']['metrics']['finalEquity'] for r in candidate_runs] == [120, 130]
    assert [r['report']['metrics']['totalReturnPct'] for r in candidate_runs] == pytest.approx([20, 30])
    q2, q3 = [r['revisionId'] for r in candidate_runs]
    frozen_view = runtime.freeze_research(ident)
    frozen = frozen_view['frozen']
    assert frozen['selectedRevisionId'] == q3
    assert len(frozen['candidates']) == 3
    assert {s['revisionId'] for s in frozen['evaluationSlots']} == {revision['id'], q3}
    assert next(c for c in frozen['candidates'] if c['revisionId'] == q2)['evaluationEligible'] is False
    before_evaluation = runs(runtime)
    assert len(before_evaluation) == initial_count + 3
    assert frozen_view['evaluation']['datasetId'] is None
    finished = runtime.evaluate_research(ident)
    assert finished['evaluation']['status'] == 'succeeded'
    assert finished['frozen'] == frozen
    slots = finished['evaluation']['slots']
    assert len(slots) == 2 and all(s['status'] == 'succeeded' for s in slots)
    evaluation_runs = {s['revisionId']: runtime.get_run(s['runId']) for s in slots}
    assert set(evaluation_runs) == {revision['id'], q3}
    assert evaluation_runs[revision['id']]['report']['metrics']['finalEquity'] == 90
    assert evaluation_runs[q3]['report']['metrics']['finalEquity'] == 70
    assert evaluation_runs[revision['id']]['report']['metrics']['totalReturnPct'] == pytest.approx(-10)
    assert evaluation_runs[q3]['report']['metrics']['totalReturnPct'] == pytest.approx(-30)
    for run in evaluation_runs.values():
        assert run['settings']['initialCash'] == 100
        assert run['report']['trades'][0]['date'] == DATES[4]
    completed_runs = copy.deepcopy(runs(runtime))
    assert len(completed_runs) == initial_count + 5
    assert set(completed_runs) - set(before_evaluation) == {s['runId'] for s in slots}
    if original:
        assert runtime.get_run(original['id']) == original
    # A fresh Runtime/store handle must restore all evidence without reexecution.
    restored = StrategyRuntime(runtime.store.root)
    assert restored.get_research(ident) == finished
    assert ident in {s['id'] for s in restored.list_research()['sessions']}
    for operation in (restored.prepare_research_training, restored.freeze_research,
                      restored.evaluate_research):
        operation(ident)
        assert runs(restored) == completed_runs
        assert restored.get_research(ident)['frozen'] == frozen
    with pytest.raises(ValueError):
        restored.submit_ai_candidates(request_id, [strategy(4)])
    assert runs(restored) == completed_runs


@pytest.mark.parametrize('provider', ['cli', 'http'])
def test_changed_holdout_never_changes_model_prompt(tmp_path, provider):
    prompts = []
    for index, tail in enumerate(((30, 20, 10), (777771, 888882, 999993))):
        runtime, _, _, _, original, created = setup_research(
            tmp_path / str(index), retrospective=True, tail=tail)
        ready = runtime.prepare_research_training(created['session']['id'])
        ident = ready['training']['aiRequestId']
        if provider == 'cli':
            preview = runtime.preview_ai_generation(ident)
        else:
            binding = GenerationBinding(
                {'provider': 'claude-api', 'model': 'synthetic-model', 'timeoutSeconds': 5,
                 'connectionId': 'a' * 32, 'maxOutputTokens': 1024},
                lambda *args, **kwargs: pytest.fail('Preview must never call model'))
            preview = ai_generation.preview_http(runtime, ident, binding)
        prompt = json.loads(preview['prompt'])
        assert prompt['metrics']['finalEquity'] == 110
        assert prompt['metrics']['totalReturnPct'] == pytest.approx(10)
        assert prompt['metrics']['finalEquity'] != original['report']['metrics']['finalEquity']
        assert 'PRIVATE_FULL_HISTORY_LABEL' not in preview['prompt']
        assert all(str(value) not in preview['prompt'] for value in (777771, 888882, 999993))
        assert DATES[-1] not in preview['prompt']
        assert len(runs(runtime)) == 2  # full historical source + training baseline only
        prompts.append(prompt)
    assert prompts[0] == prompts[1]
