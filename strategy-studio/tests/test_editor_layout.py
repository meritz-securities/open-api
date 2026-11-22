"""Display metadata contract; expected node paths are independent test fixtures."""
import copy
import hashlib
import json

import pytest
from runtime.service import StrategyRuntime


def comparison(op='gt'):
    return {'op': op, 'left': {'type': 'field', 'field': 'close'},
            'right': {'type': 'constant', 'value': 100}}


def definition():
    return {'schemaVersion': 1, 'kind': 'dsl', 'name': 'Layout contract',
            'entry': {'op': 'all', 'conditions': [comparison(), {'op': 'any', 'conditions': [comparison('lt'), comparison('gte')]}]},
            'exit': comparison('lte'), 'quantity': 1, 'allowPyramiding': False,
            'risk': {'stopLossPct': None, 'takeProfitPct': None}}


# Deliberately enumerate expected paths without using production path traversal.
PATHS = ('entry', 'entry.conditions.0', 'entry.conditions.0.left', 'entry.conditions.0.right',
         'entry.conditions.1', 'entry.conditions.1.conditions.0', 'entry.conditions.1.conditions.0.left',
         'entry.conditions.1.conditions.0.right', 'entry.conditions.1.conditions.1',
         'entry.conditions.1.conditions.1.left', 'entry.conditions.1.conditions.1.right',
         'exit', 'exit.left', 'exit.right', 'buy', 'sell')


def test_roundtrip_all_expected_nodes_and_input_isolation(tmp_path):
    service = StrategyRuntime(tmp_path)
    original = definition()
    layout = {path: {'x': index * 100, 'y': index + .25} for index, path in enumerate(PATHS)}
    expected = copy.deepcopy(layout)
    saved = service.dispatch('save_strategy', {'definition': original, 'editorLayout': layout})
    assert saved['editorLayout'] == expected
    assert saved['definition'] == original
    assert service.get_strategy(saved['id']) == saved
    assert StrategyRuntime(tmp_path).get_strategy(saved['id']) == saved
    assert service.list_strategies()['strategies'] == [saved]
    layout['entry']['x'] = 9999
    original['entry']['conditions'][0]['right']['value'] = 9999
    assert saved['editorLayout'] == expected
    assert service.get_strategy(saved['id'])['editorLayout'] == expected
    assert service.get_strategy(saved['id'])['definition']['entry']['conditions'][0]['right']['value'] == 100


@pytest.mark.parametrize('value', [0, 0.0, -0.0, .001, 9999.999, 10000, 10000.0])
def test_coordinate_inclusive_boundaries(tmp_path, value):
    service = StrategyRuntime(tmp_path)
    saved = service.save_strategy(definition(), editorLayout={'buy': {'x': value, 'y': value}})
    assert saved['editorLayout'] == {'buy': {'x': value, 'y': value}}


@pytest.mark.parametrize('value', [True, False, '1', None, [], {}, -0.001, 10000.001,
                                  float('nan'), float('inf'), -float('inf'), 10 ** 500])
@pytest.mark.parametrize('axis', ['x', 'y'])
def test_invalid_coordinate_rejected_without_partial_revision(tmp_path, value, axis):
    service = StrategyRuntime(tmp_path)
    point = {'x': 0, 'y': 0, axis: value}
    with pytest.raises(ValueError, match='유한한 숫자'):
        service.save_strategy(definition(), editorLayout={'entry': point})
    assert service.list_strategies()['strategies'] == []


@pytest.mark.parametrize('point', [{}, {'x': 1}, {'y': 1}, {'x': 0, 'y': 0, 'z': 0},
                                  {'x': 0, 'y': 0, 'code': 'never evaluate'}, [0, 0], None, '0,0'])
def test_exact_coordinate_shape(tmp_path, point):
    with pytest.raises(ValueError, match='x와 y만'):
        StrategyRuntime(tmp_path).save_strategy(definition(), editorLayout={'entry': point})


@pytest.mark.parametrize('path', ['', 'root', 'definition.entry', 'entry.conditions', 'entry.conditions.2',
                                 'entry.conditions.-1', 'entry.conditions.00', 'entry.conditions.1.conditions.02',
                                 'entry.0', 'entry.left', 'entry.conditions.0.conditions.0',
                                 'entry.conditions.0.left.value', 'exit.right.value', 'exit.conditions.0',
                                 'buy.left', 'sell.x', 'quantity', 'risk.stopLossPct', 'entry..left',
                                 '__proto__', '__class__', "__import__('os').system('never')", '../file',
                                 '/tmp/file', 1, None])
def test_only_actual_node_paths_are_accepted(tmp_path, path):
    service = StrategyRuntime(tmp_path)
    with pytest.raises(ValueError, match='현재 DSL'):
        service.save_strategy(definition(), editorLayout={path: {'x': 0, 'y': 0}})
    assert service.list_strategies()['strategies'] == []


@pytest.mark.parametrize('layout', [[], '', 5, False, [{'entry': {'x': 0, 'y': 0}}]])
def test_layout_requires_object(tmp_path, layout):
    with pytest.raises(ValueError, match='최대 128'):
        StrategyRuntime(tmp_path).save_strategy(definition(), editorLayout=layout)


def test_layout_absolute_limit_checked_before_paths(tmp_path):
    service = StrategyRuntime(tmp_path)
    oversized = {f'unknown{index}': {'x': 0, 'y': 0} for index in range(129)}
    with pytest.raises(ValueError, match='최대 128'):
        service.save_strategy(definition(), editorLayout=oversized)
    # 128 respects the absolute count bound but must still pass actual-path validation.
    oversized.pop('unknown128')
    with pytest.raises(ValueError, match='현재 DSL'):
        service.save_strategy(definition(), editorLayout=oversized)
    assert service.list_strategies()['strategies'] == []


def test_existing_64_node_dsl_limit_is_not_expanded(tmp_path):
    service = StrategyRuntime(tmp_path)
    maximum = definition()
    maximum['entry'] = {'op': 'all', 'conditions': [comparison() for _ in range(20)]}
    # 1 entry group + 20*(1 comparison + 2 operands) + 3 exit nodes = 64.
    paths = ['entry', 'exit', 'exit.left', 'exit.right', 'buy', 'sell']
    paths += [f'entry.conditions.{index}{suffix}' for index in range(20) for suffix in ('', '.left', '.right')]
    assert len(paths) == 66
    saved = service.save_strategy(maximum, editorLayout={path: {'x': 0, 'y': 10000} for path in paths})
    assert len(saved['editorLayout']) == 66
    maximum['entry']['conditions'].append(comparison())
    with pytest.raises(ValueError, match='depth/node limit'):
        service.save_strategy(maximum, editorLayout={})


def test_absent_null_and_empty_layout_preserve_legacy_behavior(tmp_path):
    service = StrategyRuntime(tmp_path)
    legacy = service.save_strategy(definition())
    explicit_null = service.save_strategy(definition(), editorLayout=None)
    expected_fields = {'id', 'strategyId', 'parentRevisionId', 'definition', 'hash', 'createdAt'}
    assert set(legacy) == set(explicit_null) == expected_fields
    empty = service.save_strategy(definition(), editorLayout={})
    assert set(empty) == expected_fields | {'editorLayout'}
    assert empty['editorLayout'] == {}
    assert legacy['hash'] == explicit_null['hash'] == empty['hash']


def test_python_rejects_even_empty_layout_but_legacy_python_still_saves(tmp_path):
    service = StrategyRuntime(tmp_path)
    python = {'schemaVersion': 1, 'kind': 'python', 'name': 'Not executed',
              'code': 'def on_bar(ctx):\n    return {"action":"hold"}\n'}
    for layout in ({}, {'buy': {'x': 0, 'y': 0}}):
        with pytest.raises(ValueError, match='DSL 전략에만'):
            service.save_strategy(python, editorLayout=layout)
    assert 'editorLayout' not in service.save_strategy(python)
    assert 'editorLayout' not in service.save_strategy(python, editorLayout=None)


def test_layout_changes_do_not_change_definition_hash_or_execution(tmp_path):
    service = StrategyRuntime(tmp_path)
    original = definition()
    first = service.save_strategy(original, editorLayout={'buy': {'x': 0, 'y': 0}})
    second = service.save_strategy(original, first['id'], {'buy': {'x': 10000, 'y': 10000}})
    expected_hash = hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True,
                                              separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert first['hash'] == second['hash'] == expected_hash
    assert first['id'] != second['id'] and first['strategyId'] == second['strategyId']
    assert second['parentRevisionId'] == first['id']
    assert service.get_strategy(first['id']) == first
    assert service.get_strategy(second['id']) == second
    no_inheritance = service.save_strategy(original, second['id'])
    assert 'editorLayout' not in no_inheritance
    data = service.demo_data()
    run1 = service.run(first['id'], data['id'])
    run2 = service.run(second['id'], data['id'])
    assert run1['manifest']['strategyHash'] == run2['manifest']['strategyHash'] == expected_hash
    assert run1['manifest']['resultHash'] == run2['manifest']['resultHash']
    assert run1['report'] == run2['report']


def test_old_paths_are_rejected_after_definition_structure_changes(tmp_path):
    changed = definition()
    changed['entry'] = comparison()
    with pytest.raises(ValueError, match='현재 DSL'):
        StrategyRuntime(tmp_path).save_strategy(changed, editorLayout={'entry.conditions.0': {'x': 10, 'y': 20}})


def test_definition_validation_still_precedes_layout_and_hash_check_is_unchanged(tmp_path, monkeypatch):
    service = StrategyRuntime(tmp_path)
    malformed = definition()
    malformed['entry']['code'] = 'not an allowed definition field'
    with pytest.raises(ValueError, match='invalid condition'):
        service.save_strategy(malformed, editorLayout={})
    stored = service.save_strategy(definition(), editorLayout={'entry': {'x': 1, 'y': 2}})
    corrupted = copy.deepcopy(stored)
    corrupted['definition']['name'] = 'changed behind storage'
    monkeypatch.setattr(service.store, 'get', lambda kind, ident: corrupted)
    with pytest.raises(ValueError, match='무결성 검사 실패'):
        service.get_strategy(stored['id'])
