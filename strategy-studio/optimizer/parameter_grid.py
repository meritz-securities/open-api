"""Bounded DSL parameter enumeration; execution/accounting stay in the Runtime."""
import copy
import itertools
import math


def parameter_paths(definition):
    """Enumerate actual numeric parameter locations; never interpret arbitrary paths."""
    from runtime.dsl import INDICATORS
    paths = {'quantity': ('quantity',), 'risk.stopLossPct': ('risk', 'stopLossPct'),
             'risk.takeProfitPct': ('risk', 'takeProfitPct')}
    def visit(node, parts):
        if node.get('op') in ('all', 'any'):
            for index, child in enumerate(node['conditions']):
                visit(child, (*parts, 'conditions', index))
        elif 'op' in node:
            for side in ('left', 'right'):
                operand = node[side]
                kind = operand['type']
                if kind == 'constant':
                    leaves = ('value',)
                elif kind in INDICATORS:
                    # Each numeric indicator parameter present in the operand is a
                    # tunable axis; the 'component' selector (a string) is not.
                    leaves = tuple(name for name in INDICATORS[kind][2] if name in operand)
                else:
                    leaves = ()
                for leaf in leaves:
                    target = (*parts, side, leaf)
                    paths['.'.join(map(str, target))] = target
    visit(definition['entry'], ('entry',))
    visit(definition['exit'], ('exit',))
    return paths


def plan_grid(definition, axes=None, maxEvaluations=12):
    from runtime.dsl import validate_definition, OperandCombinationError
    original = validate_definition(definition)
    if original['kind'] != 'dsl':
        raise ValueError('parameter_grid supports DSL strategies only')
    if type(maxEvaluations) is not int or not 1 <= maxEvaluations <= 12:
        raise ValueError('maxEvaluations must be an integer from 1 to 12')
    if type(axes) is not list or not 1 <= len(axes) <= 3:
        raise ValueError('parameter_grid requires 1..3 axes')
    allowed = parameter_paths(original)
    seen = set()
    count = 1
    for axis in axes:
        if type(axis) is not dict or set(axis) != {'path', 'values'}:
            raise ValueError('each axis requires only path and values')
        path, values = axis['path'], axis['values']
        if not isinstance(path, str) or path not in allowed or path in seen:
            raise ValueError('axis path must identify a distinct supported strategy parameter')
        seen.add(path)
        if type(values) is not list or not 1 <= len(values) <= 12:
            raise ValueError('each axis requires 1..12 values')
        if any(type(value) not in (int, float) or abs(value) > 1e12 or not math.isfinite(value) for value in values):
            raise ValueError('axis values must be finite numbers, not booleans')
        if len(set(values)) != len(values):
            raise ValueError('axis values must be unique')
        count *= len(values)
        if count > maxEvaluations:
            raise ValueError('parameter combinations exceed maxEvaluations')
    plans = []
    for values in itertools.product(*(axis['values'] for axis in axes)):
        candidate = copy.deepcopy(original)
        parameters = {}
        for axis, value in zip(axes, values):
            parts = allowed[axis['path']]
            target = candidate
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = value
            parameters[axis['path']] = value
        # Reject the whole plan before writing jobs/revisions if any value is invalid,
        # but a grid cell that is merely an infeasible fast>=slow combination is
        # skipped (not fatal) so a sweep over those axes keeps its valid cells.
        try:
            candidate = validate_definition(candidate)
        except OperandCombinationError:
            continue
        plans.append({'index': len(plans) + 1, 'parameterValues': parameters, 'definition': candidate})
    return plans


def evaluate_grid(plans, rerun, cancel, progress):
    results = []
    for plan in plans:
        cancel()
        item = {key: copy.deepcopy(plan[key]) for key in ('index', 'parameterValues')}
        value = None
        try:
            value = rerun(plan)
            score = value.get('metrics', {}).get('totalReturnPct')
            if value.get('status') != 'succeeded' or type(score) not in (int, float) or not math.isfinite(score):
                raise ValueError('finite successful metric required')
            item.update(status='succeeded', revisionId=value['revisionId'], result=value)
        except Exception as error:
            cancel()
            item.update(status='failed', error='candidate execution failed')
            for key, attribute in (('runId', 'run_id'), ('revisionId', 'revision_id')):
                ident = getattr(error, attribute, None)
                if isinstance(ident, str):
                    item[key] = ident
        results.append(item)
        progress(copy.deepcopy(results))
        cancel()
    complete = all(item['status'] == 'succeeded' for item in results)
    winner = max(results, key=lambda item: item['result']['metrics']['totalReturnPct']) if complete else None
    return {'method': 'parameter-grid', 'status': 'completed' if complete else 'completed_with_failures',
            'selectionMetric': 'totalReturnPct', 'tieBreak': 'candidate input order',
            'candidateCount': len(plans), 'results': results,
            'bestCandidateIndex': winner['index'] if winner else None,
            'holdoutContaminated': True,
            'assumptions': ['Every candidate uses the original stored data and cost settings with independent initial cash.',
                            'All combinations are evaluated in input order; the first axis is outermost. Failed and worse candidates remain visible.',
                            'Selection uses the same observed period. This is in-sample research, not evidence of future performance or an untouched holdout.',
                            'The elapsed-time budget is cooperative, checked between engine decisions and candidate runs; it is not hard process termination.']}
