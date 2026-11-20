"""DSL + SDK coverage for the expanded indicator library (Claude-authored).

Exercises every INDICATORS operand type end to end: schema validation, decider
evaluation on synthetic candles, backward compatibility of existing strategies,
the volume caveat, SDK get_indicator parity, and parameter-grid enumeration.
"""
import importlib.util
import json
import math
from pathlib import Path

import pytest

from runtime.dsl import INDICATORS, _value, make_decider, validate_definition, templates
from optimizer.parameter_grid import parameter_paths, plan_grid


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def candles(n=80, volume=True):
    """Deterministic OHLC(V) candles, past->present, enough bars for warmups."""
    bars = []
    price = 100.0
    for i in range(n):
        price += math.sin(i / 3.0) * 1.5 + (0.3 if i % 2 == 0 else -0.2)
        o = price - 0.5
        c = price + 0.4
        h = max(o, c) + 0.6
        l = min(o, c) - 0.6
        bar = {'open': round(o, 4), 'high': round(h, 4), 'low': round(l, 4), 'close': round(c, 4)}
        if volume:
            bar['volume'] = 1000 + i * 3
        bars.append(bar)
    return bars


def valid_operand(name, with_optional=False):
    """Build a minimally valid operand for an indicator type."""
    _fn, _inputs, params, required, components = INDICATORS[name]
    operand = {'type': name}
    for key in required:
        operand[key] = 10  # 'period' for the plain moving averages / stdev
    if with_optional:
        for key, (kind, lo, hi) in params.items():
            if key in operand:
                continue
            if kind == 'int':
                # keep fast < slow for the oscillators that require it
                operand[key] = 3 if key == 'fast' else 9 if key == 'slow' else 5
            else:
                operand[key] = round((lo + hi) / 2 if hi < 1e6 else 1.0, 4)
    if components is not None:
        operand['component'] = components[0]
    return operand


def wrap(operand):
    """A full DSL definition comparing an operand against a constant."""
    return {
        'schemaVersion': 1, 'kind': 'dsl', 'name': 'indicator test',
        'entry': {'op': 'gt', 'left': operand, 'right': {'type': 'constant', 'value': 0}},
        'exit': {'op': 'lt', 'left': operand, 'right': {'type': 'constant', 'value': 0}},
    }


def run_decider(definition, bars, use_cache=False):
    decide = make_decider(definition, bars=bars) if use_cache else make_decider(definition)
    actions = set()
    for index in range(len(bars)):
        ctx = {'index': index, 'history': bars[:index + 1],
               'portfolio': {'shares': 0, 'averageCost': 0}}
        actions.add(decide(ctx)['action'])
    return actions


# --------------------------------------------------------------------------- #
# every indicator validates and evaluates
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('name', sorted(INDICATORS))
def test_every_indicator_validates_and_decides(name):
    bars = candles(volume=True)  # volume present so volume ops are meaningful
    for with_optional in (False, True):
        operand = valid_operand(name, with_optional=with_optional)
        definition = validate_definition(wrap(operand))
        assert definition['entry']['left']['type'] == name
        actions = run_decider(definition, bars)
        assert actions <= {'buy', 'sell', 'hold'}


def test_new_operands_also_run_through_cached_precompute_path():
    # The production path precomputes all operands, including new indicators.
    bars = candles()
    for name in ('macd', 'bollinger', 'atr', 'stochastic', 'wma', 'vwap'):
        definition = validate_definition(wrap(valid_operand(name)))
        assert run_decider(definition, bars, use_cache=True) <= {'buy', 'sell', 'hold'}


def test_multi_output_component_selects_distinct_series():
    bars = candles()
    # macd.macd and macd.signal are generally different series.
    macd_line = _value({'type': 'macd', 'component': 'macd'}, bars)
    macd_signal = _value({'type': 'macd', 'component': 'signal'}, bars)
    assert macd_line is not None and macd_signal is not None
    assert macd_line != macd_signal


def test_single_series_values_match_calc_directly():
    from indicators import calc
    bars = candles()
    closes = [b['close'] for b in bars]
    assert _value({'type': 'wma', 'period': 10}, bars) == pytest.approx(calc.wma(closes, 10)[-1])
    highs = [b['high'] for b in bars]
    lows = [b['low'] for b in bars]
    assert _value({'type': 'atr', 'period': 14}, bars) == pytest.approx(
        calc.atr(highs, lows, closes, 14)[-1])
    assert _value({'type': 'bollinger', 'component': 'upper', 'period': 20, 'num_std': 2}, bars) \
        == pytest.approx(calc.bollinger(closes, 20, 2)['upper'][-1])


# --------------------------------------------------------------------------- #
# validation rejects malformed operands
# --------------------------------------------------------------------------- #
def test_validation_rejects_bad_operands():
    bad = [
        {'type': 'wma'},                                  # missing required period
        {'type': 'sma', 'period': 1},                     # period below range
        {'type': 'sma', 'period': 251},                   # period above range
        {'type': 'sma', 'period': 10, 'extra': 1},        # unknown param
        {'type': 'sma', 'period': 10.0},                  # period must be int
        {'type': 'sma', 'period': True},                  # bool is not int
        {'type': 'macd'},                                 # multi needs component
        {'type': 'macd', 'component': 'nope'},            # unknown component
        {'type': 'wma', 'period': 10, 'component': 'x'},  # component on single-series
        {'type': 't3', 'period': 10, 'vfactor': 5},       # fraction out of range
        {'type': 'bollinger', 'component': 'upper', 'num_std': 1000},  # multiplier out of range
        {'type': 'nope', 'period': 10},                   # unknown indicator
    ]
    for operand in bad:
        with pytest.raises(ValueError):
            validate_definition(wrap(operand))


def test_backward_compatible_sma_schema_exact():
    # Still exactly {'type','period'} with an int in 2..250, like the original.
    assert validate_definition(wrap({'type': 'sma', 'period': 5}))['entry']['left'] \
        == {'type': 'sma', 'period': 5}
    with pytest.raises(ValueError):
        validate_definition(wrap({'type': 'sma', 'period': 5, 'field': 'close'}))


# --------------------------------------------------------------------------- #
# volume caveat
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('name', ['obv', 'vwap', 'mfi', 'cmf', 'force_index',
                                  'ease_of_movement', 'ad_line', 'chaikin_oscillator', 'pvt', 'vwma'])
def test_volume_operands_valid_in_schema_but_need_volume(name):
    operand = valid_operand(name)
    definition = validate_definition(wrap(operand))  # validation is data-independent
    with_vol = candles(volume=True)
    without_vol = candles(volume=False)
    # Meaningful with volume ...
    assert run_decider(definition, with_vol) <= {'buy', 'sell', 'hold'}
    # ... and a clear error (not a silent wrong answer) without it.
    with pytest.raises(ValueError):
        run_decider(definition, without_vol)


# --------------------------------------------------------------------------- #
# existing strategy library stays valid and identical
# --------------------------------------------------------------------------- #
def test_existing_strategies_still_validate():
    root = Path(__file__).resolve().parent.parent / 'strategies'
    files = sorted(root.glob('*.json'))
    assert files
    for path in files:
        definition = json.loads(path.read_text(encoding='utf-8'))
        validate_definition(definition)  # must not raise
    assert templates()  # template loader still succeeds


def test_legacy_sma_cross_decides_identically():
    bars = candles(volume=False)
    definition = {
        'schemaVersion': 1, 'kind': 'dsl', 'name': 'sma cross',
        'entry': {'op': 'cross_above', 'left': {'type': 'sma', 'period': 5},
                  'right': {'type': 'sma', 'period': 20}},
        'exit': {'op': 'cross_below', 'left': {'type': 'sma', 'period': 5},
                 'right': {'type': 'sma', 'period': 20}},
    }
    cached = make_decider(definition, bars=bars)
    plain = make_decider(definition)
    for index in range(len(bars)):
        ctx = {'index': index, 'history': bars[:index + 1],
               'portfolio': {'shares': 0, 'averageCost': 0}}
        assert cached(ctx) == plain(ctx)


# --------------------------------------------------------------------------- #
# parameter grid sees the new params
# --------------------------------------------------------------------------- #
def test_parameter_paths_enumerate_new_indicator_params():
    operand = {'type': 'macd', 'component': 'signal', 'fast': 12, 'slow': 26, 'signal': 9}
    definition = validate_definition(wrap(operand))
    paths = parameter_paths(definition)
    assert 'entry.left.fast' in paths
    assert 'entry.left.slow' in paths
    assert 'entry.left.signal' in paths
    # The string component selector is never a numeric axis.
    assert 'entry.left.component' not in paths


def test_plan_grid_optimizes_a_new_indicator_param():
    operand = {'type': 'bollinger', 'component': 'upper', 'period': 20, 'num_std': 2}
    definition = validate_definition(wrap(operand))
    plans = plan_grid(definition, axes=[{'path': 'entry.left.period', 'values': [10, 20, 30]}],
                      maxEvaluations=12)
    assert len(plans) == 3
    assert [p['parameterValues']['entry.left.period'] for p in plans] == [10, 20, 30]
    for plan in plans:
        validate_definition(plan['definition'])


# --------------------------------------------------------------------------- #
# SDK get_indicator mirrors the DSL
# --------------------------------------------------------------------------- #
def _load_sdk():
    spec = importlib.util.spec_from_file_location(
        'meritz_strategy_test', Path(__file__).parents[1] / 'sdk/meritz_strategy/__init__.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sdk_get_indicator_matches_dsl_value():
    sdk = _load_sdk()
    bars = candles(volume=True)
    observation = {'index': len(bars) - 1, 'date': '2025-01-01', 'bar': bars[-1],
                   'history': bars, 'portfolio': {'cash': 0, 'shares': 0, 'averageCost': 0}}
    ctx = sdk.Context(observation)
    # single-series price, OHLC, multi-output, and volume indicators all mirror _value
    assert ctx.get_indicator('wma', 10) == pytest.approx(_value({'type': 'wma', 'period': 10}, bars))
    assert ctx.get_indicator('atr', period=14) == pytest.approx(
        _value({'type': 'atr', 'period': 14}, bars))
    assert ctx.get_indicator('macd', component='signal') == pytest.approx(
        _value({'type': 'macd', 'component': 'signal'}, bars))
    assert ctx.get_indicator('obv') == pytest.approx(_value({'type': 'obv'}, bars))
    # backward compatible defaults
    assert ctx.get_indicator('sma', 2) is not None


def test_sdk_get_indicator_errors():
    sdk = _load_sdk()
    novol = candles(volume=False)
    ctx = sdk.Context({'index': len(novol) - 1, 'date': 'x', 'bar': novol[-1],
                       'history': novol, 'portfolio': {'cash': 0, 'shares': 0, 'averageCost': 0}})
    with pytest.raises(ValueError):
        ctx.get_indicator('unknown', 2)                 # unchanged behaviour
    with pytest.raises(ValueError):
        ctx.get_indicator('macd')                       # multi needs component
    with pytest.raises(ValueError):
        ctx.get_indicator('wma', component='x')         # single has no component
    with pytest.raises(ValueError):
        ctx.get_indicator('obv')                        # volume missing -> clear error
    with pytest.raises(ValueError):
        ctx.get_indicator('sma', 2, component='macd')   # legacy path rejects extras


# --------------------------------------------------------------------------- #
# MAJOR-1: published JSON schema is regenerated from the registry
# --------------------------------------------------------------------------- #
def test_published_schema_operand_matches_registry():
    from runtime.dsl import operand_schema_oneof, strategy_schema
    published = strategy_schema()['$defs']['operand']['oneOf']
    assert published == operand_schema_oneof()      # no drift between file and INDICATORS
    types = {branch['properties']['type'].get('const') for branch in published
             if 'const' in branch['properties']['type']}
    assert {'field', 'constant'} <= types
    assert set(INDICATORS) <= types                 # every indicator reachable via the public schema


def test_all_strategies_validate_against_published_schema():
    import jsonschema
    from runtime.dsl import strategy_schema
    validator = jsonschema.Draft202012Validator(strategy_schema())
    files = sorted((Path(__file__).resolve().parents[1] / 'strategies').glob('*.json'))
    assert len(files) >= 28
    for path in files:
        validator.validate(json.loads(path.read_text(encoding='utf-8')))  # raises on failure


# --------------------------------------------------------------------------- #
# MAJOR-2: all templates stay fast at 5000 bars (precompute, not O(n^2))
# --------------------------------------------------------------------------- #
def test_all_templates_decide_within_deadline_on_5000_bars():
    import time
    bars = candles(5000, volume=True)
    definitions = templates()
    assert len(definitions) >= 28
    deadline = time.monotonic() + 60.0
    for definition in definitions:
        decide = make_decider(definition, bars=bars)   # production path uses bars=
        for index in range(len(bars)):
            ctx = {'index': index, 'history': bars[:index + 1],
                   'portfolio': {'shares': 0, 'averageCost': 0}}
            assert decide(ctx)['action'] in ('buy', 'sell', 'hold')
        assert time.monotonic() < deadline, 'template decisions exceeded the 60s deadline'


def test_precompute_matches_uncached_decisions_for_new_operands():
    bars = candles(220)
    for name in ('macd', 'bollinger', 'atr', 'stochastic', 'adx', 'hma', 'keltner', 'vortex', 'ichimoku'):
        definition = validate_definition(wrap(valid_operand(name)))
        cached = make_decider(definition, bars=bars)
        plain = make_decider(definition)
        for index in range(len(bars)):
            ctx = {'index': index, 'history': bars[:index + 1],
                   'portfolio': {'shares': 0, 'averageCost': 0}}
            assert cached(ctx) == plain(ctx)            # identical decisions + evidence


@pytest.mark.parametrize('name', sorted(INDICATORS))
def test_precomputed_indicator_is_prefix_causal_for_every_indicator(name):
    bars = candles(110, volume=True)
    components = INDICATORS[name][4] or (None,)
    for with_optional in (False, True):
        for component in components:
            operand = valid_operand(name, with_optional=with_optional)
            if component is not None:
                operand['component'] = component
            definition = validate_definition(wrap(operand))
            cached = make_decider(definition, bars=bars)
            plain = make_decider(definition)
            for index in range(len(bars)):
                ctx = {'index': index, 'history': bars[:index + 1],
                       'portfolio': {'shares': 0, 'averageCost': 0}}
                decision = cached(ctx)
                assert decision == plain(ctx), (name, component, with_optional)
                if decision['evidence']['rules'][0]['left'] is not None:
                    assert isinstance(decision['evidence']['rules'][0]['left'], (int, float)), name
                    if index == len(bars) - 1:
                        has_value = True
                if index == 0:
                    has_value = False
            assert has_value, f'{name}.{component} produced no defined value (optional={with_optional})'
            # Mutating only future prices and volume must not alter a past output.
            for index in (0, 10, 55):
                changed = [dict(bar) for bar in bars]
                for future_index in range(index + 1, len(changed)):
                    price = changed[future_index]['close'] + 100
                    changed[future_index].update(open=price, high=price + 1, low=price - 1, close=price,
                                                 volume=changed[future_index]['volume'] + 10000)
                future_changed = make_decider(definition, bars=changed)
                ctx = {'index': index, 'history': bars[:index + 1],
                       'portfolio': {'shares': 0, 'averageCost': 0}}
                assert cached(ctx) == future_changed(ctx), (name, component, with_optional, index)


# --------------------------------------------------------------------------- #
# MAJOR-3: SDK uses calc defaults (no period override) and mirrors the registry
# --------------------------------------------------------------------------- #
def test_sdk_registry_mirrors_runtime_registry():
    sdk = _load_sdk()
    assert sdk._INDICATORS == INDICATORS
    assert sdk._FAST_SLOW == {'macd', 'ppo', 'awesome_oscillator', 'chaikin_oscillator'}


def test_sdk_dsl_value_parity_across_all_indicators():
    sdk = _load_sdk()
    bars = candles(120, volume=True)
    ctx = sdk.Context({'index': len(bars) - 1, 'date': 'x', 'bar': bars[-1],
                       'history': bars, 'portfolio': {'cash': 0, 'shares': 0, 'averageCost': 0}})
    for name in sorted(INDICATORS):
        _fn, _inputs, _params, required, components = INDICATORS[name]
        operand = {'type': name}
        for key in required:
            operand[key] = 10
        component = components[0] if components is not None else None
        if component is not None:
            operand['component'] = component
        dsl_val = _value(operand, bars)
        sdk_val = ctx.get_indicator(name, period=operand.get('period'), component=component)
        if dsl_val is None:
            assert sdk_val is None, name
        else:
            assert sdk_val == pytest.approx(dsl_val), name


def test_sdk_default_period_follows_calc_not_twenty():
    # Regression for the old period=20 injection: atr's default is 14, so an
    # SDK call with no period must equal the DSL default-param value, not atr(20).
    from indicators import calc
    sdk = _load_sdk()
    bars = candles(120, volume=True)
    ctx = sdk.Context({'index': len(bars) - 1, 'date': 'x', 'bar': bars[-1],
                       'history': bars, 'portfolio': {'cash': 0, 'shares': 0, 'averageCost': 0}})
    highs = [b['high'] for b in bars]
    lows = [b['low'] for b in bars]
    closes = [b['close'] for b in bars]
    assert ctx.get_indicator('atr') == pytest.approx(calc.atr(highs, lows, closes, 14)[-1])
    assert ctx.get_indicator('atr') != pytest.approx(calc.atr(highs, lows, closes, 20)[-1])


# --------------------------------------------------------------------------- #
# MINOR-3: fast < slow validation + optimizer skips infeasible cells
# --------------------------------------------------------------------------- #
def test_fast_slow_combination_rejected():
    from runtime.dsl import OperandCombinationError
    for name, comp in (('macd', 'macd'), ('ppo', 'ppo')):
        with pytest.raises(OperandCombinationError):
            validate_definition(wrap({'type': name, 'component': comp, 'fast': 26, 'slow': 12, 'signal': 9}))
    with pytest.raises(ValueError):
        validate_definition(wrap({'type': 'awesome_oscillator', 'fast': 34, 'slow': 5}))


def test_parameter_grid_skips_infeasible_fast_slow_cells():
    operand = {'type': 'macd', 'component': 'macd', 'fast': 12, 'slow': 26, 'signal': 9}
    definition = validate_definition(wrap(operand))
    # slow fixed at 26; fast=30 is infeasible (>=slow) and must be skipped, not fatal.
    plans = plan_grid(definition, axes=[{'path': 'entry.left.fast', 'values': [8, 20, 30]}],
                      maxEvaluations=12)
    assert [p['parameterValues']['entry.left.fast'] for p in plans] == [8, 20]
    assert [p['index'] for p in plans] == [1, 2]         # re-numbered contiguously
