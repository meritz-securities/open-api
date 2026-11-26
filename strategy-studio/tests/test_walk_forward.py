"""Independent selection oracles; the accounting engine is not the test oracle."""
import copy
import json

import pytest

from optimizer import walk_forward


def bars(count=11):
    return [{'date': f'2025-01-{index + 1:02d}', 'observation': index} for index in range(count)]


def outcome(ident, part, score):
    return {'runId': f'{ident}-{part[0]["date"]}', 'metrics': {'totalReturnPct': score},
            'status': 'succeeded', 'report': {'shouldNotBeInResponse': True}}


def test_independent_oracle_selects_different_winners_and_preserves_windows():
    calls = []
    def rerun(ident, part):
        observed = [bar['observation'] for bar in part]
        calls.append((ident, observed))
        # Training windows are 0..3, 2..5, 4..7. The table is a handwritten
        # selection oracle, independent from both engine and helper logic.
        scores = {'a': {0: 9, 2: 1, 4: 2}, 'b': {0: 4, 2: 8, 4: 7}}
        score = scores[ident][observed[0]] if len(part) == 4 else -50
        return outcome(ident, part, score)
    data = bars()
    original = copy.deepcopy(data)
    result = walk_forward(data, ['a', 'b'], rerun, trainBars=4, testBars=2)
    assert calls == [('a', [0, 1, 2, 3]), ('b', [0, 1, 2, 3]), ('a', [4, 5]),
                     ('a', [2, 3, 4, 5]), ('b', [2, 3, 4, 5]), ('b', [6, 7]),
                     ('a', [4, 5, 6, 7]), ('b', [4, 5, 6, 7]), ('b', [8, 9])]
    assert [fold['selectedRevisionId'] for fold in result['folds']] == ['a', 'b', 'b']
    assert all(fold['test']['result']['metrics']['totalReturnPct'] == -50 for fold in result['folds'])
    assert result['droppedTailBars'] == 1 and result['status'] == 'completed'
    assert result['folds'][0]['train'] == {'startDate': '2025-01-01', 'endDate': '2025-01-04', 'barCount': 4}
    assert result['folds'][0]['testWindow'] == {'startDate': '2025-01-05', 'endDate': '2025-01-06', 'barCount': 2}
    assert data == original
    assert 'shouldNotBeInResponse' not in json.dumps(result)
    assert 'equity' not in result and 'aggregateReturnPct' not in result


def test_holdout_sentinel_cannot_affect_training_and_mutation_is_isolated():
    data = bars(6)
    data[-1]['secret'] = 'test-only'
    seen = []
    def rerun(ident, part):
        seen.append(copy.deepcopy(part))
        if len(part) == 4:
            assert not any('secret' in bar for bar in part)
            assert part[0]['observation'] == 0
            part[0]['observation'] = 999  # A callback cannot poison other runs.
            return outcome(ident, part, 10 if ident == 'a' else 9)
        assert ident == 'a'  # The unseen candidate never gets a test comparison.
        assert part[-1]['secret'] == 'test-only'
        return outcome(ident, part, -1000)
    result = walk_forward(data, ['a', 'b'], rerun, trainBars=4, testBars=2)
    assert result['folds'][0]['selectedRevisionId'] == 'a'
    assert len(seen) == 3 and data[0]['observation'] == 0


def test_equal_training_scores_use_input_order_and_are_repeatable():
    def rerun(ident, part):
        return outcome(ident, part, 0)
    first = walk_forward(bars(6), ['second', 'first'], rerun, trainBars=4, testBars=2)
    assert first == walk_forward(bars(6), ['second', 'first'], rerun, trainBars=4, testBars=2)
    assert first['folds'][0]['selectedRevisionId'] == 'second'


class FailedRun(ValueError):
    run_id = 'persisted-failed-run'


def test_any_failed_training_candidate_prevents_selection_but_keeps_other_folds():
    calls = []
    def rerun(ident, part):
        calls.append((ident, part[0]['observation'], len(part)))
        if ident == 'a' and part[0]['observation'] == 0:
            raise FailedRun('sensitive internal error must not be returned')
        return outcome(ident, part, 1)
    result = walk_forward(bars(8), ['a', 'b'], rerun, trainBars=4, testBars=2)
    first, second = result['folds']
    assert calls == [('a', 0, 4), ('b', 0, 4), ('a', 2, 4), ('b', 2, 4), ('a', 6, 2)]
    assert first['status'] == 'failed' and first['test'] is None and first['selectedRevisionId'] is None
    assert first['training'][0]['runId'] == 'persisted-failed-run'
    assert second['status'] == 'succeeded'
    assert result['status'] == 'completed_with_failures'
    assert 'sensitive' not in json.dumps(result)


def test_failed_test_keeps_selected_training_candidate_and_failed_run_id():
    def rerun(ident, part):
        if len(part) == 2:
            raise FailedRun('test failed')
        return outcome(ident, part, 5)
    result = walk_forward(bars(6), ['a'], rerun, trainBars=4, testBars=2)
    fold = result['folds'][0]
    assert fold['selectedRevisionId'] == 'a' and fold['test']['runId'] == 'persisted-failed-run'
    assert fold['status'] == 'failed'


@pytest.mark.parametrize('score', [None, float('inf'), float('-inf'), float('nan'), True, '7'])
def test_nonfinite_or_invalid_training_metric_cannot_win(score):
    result = walk_forward(bars(6), ['a'], lambda ident, part: outcome(ident, part, score), trainBars=4, testBars=2)
    fold = result['folds'][0]
    assert fold['selectedRevisionId'] is None and fold['test'] is None
    assert fold['training'][0]['runId'].startswith('a-')
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('changes', [
    {'candidate_ids': []}, {'candidate_ids': ['a', 'a']}, {'candidate_ids': ['a'] * 5},
    {'candidate_ids': ['']}, {'candidate_ids': [None]}, {'candidate_ids': 'a'},
    {'trainBars': True}, {'trainBars': 1}, {'trainBars': 4.0}, {'testBars': 0},
    {'testBars': False}, {'testBars': 3.0}, {'trainBars': 100},
    {'bars': bars(3)}, {'bars': [{}] * 6}, {'bars': bars(6)[::-1]},
    {'bars': bars(6) + [bars(6)[-1]]}, {'bars': bars(6) * 1000},
    {'bars': bars(18), 'trainBars': 4, 'testBars': 2},  # Seven full folds.
    {'rerun': None}, {'cancel': 1},
])
def test_invalid_options_fail_before_any_execution(changes):
    calls = []
    def rerun(*args):
        calls.append(args)
        raise AssertionError('must not execute')
    arguments = {'bars': bars(6), 'candidate_ids': ['a'], 'rerun': rerun, 'trainBars': 4, 'testBars': 2}
    arguments.update(changes)
    with pytest.raises(ValueError):
        walk_forward(**arguments)
    assert calls == []


def test_six_folds_boundary_and_four_candidates_are_allowed():
    result = walk_forward(bars(16), ['a', 'b', 'c', 'd'], lambda ident, part: outcome(ident, part, 0), trainBars=4, testBars=2)
    assert len(result['folds']) == 6 and result['droppedTailBars'] == 0
    assert all(len(fold['training']) == 4 for fold in result['folds'])


@pytest.mark.parametrize('during_exception', [False, True])
def test_cancellation_during_callback_propagates_without_more_candidates(during_exception):
    stopped = False
    calls = []
    class Cancelled(Exception):
        pass
    def checkpoint():
        if stopped:
            raise Cancelled()
    def rerun(ident, part):
        nonlocal stopped
        calls.append(ident)
        stopped = True
        if during_exception:
            raise FailedRun('cancelled worker')
        return outcome(ident, part, 0)
    with pytest.raises(Cancelled):
        walk_forward(bars(6), ['a', 'b'], rerun, trainBars=4, testBars=2, cancel=checkpoint)
    assert calls == ['a']


def test_cancellation_before_first_execution():
    def checkpoint():
        raise RuntimeError('cancel requested')
    with pytest.raises(RuntimeError, match='cancel requested'):
        walk_forward(bars(6), ['a'], lambda *args: pytest.fail('must not run'), trainBars=4, testBars=2, cancel=checkpoint)
