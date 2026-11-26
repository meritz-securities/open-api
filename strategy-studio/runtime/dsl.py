"""Closed, bounded strategy DSL. Never evaluates user expressions or source."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
from indicators import calc


class StrategyValidationError(ValueError):
    """Preserve parser diagnostics across the shared Studio/CLI/MCP contract."""

    def __init__(self, errors):
        self.errors = copy.deepcopy(errors)
        super().__init__('; '.join(error['message'] for error in errors))


# ---------------------------------------------------------------------------
# Extended indicator operand registry.
#
# Each entry describes one DSL operand type backed by a pure-stdlib function in
# indicators/calc.py. Tuple layout: (calc_fn, inputs, params, required, components)
#   calc_fn    : attribute name on indicators.calc
#   inputs     : ordered positional price series the function consumes, drawn from
#                {'open','high','low','close','volume'}. DSL always sources them
#                from the corresponding bar field (close-only indicators use close).
#   params     : {name: (kind, lo, hi)} where kind is 'int' or 'num'. Names match
#                the calc keyword arguments exactly, so they map straight through.
#   required   : params that MUST be present. Every other param is optional and
#                falls back to the calc function's standard default when omitted.
#   components : for multi-output indicators (calc returns a dict), the allowed
#                values of the mandatory 'component' selector; None for single
#                series indicators (calc returns a list).
#
# Backward compatibility: sma/ema/rsi keep exactly their historical schema
# ({'type','period'} with period an int in 2..250) and evaluation path.
# ---------------------------------------------------------------------------
_W = ('int', 2, 250)        # primary lookback window
_S = ('int', 1, 250)        # smoothing / sub-window (1 == no smoothing)
_ANN = ('int', 1, 100_000)  # annualization factor (trading periods per year)
_MUL = ('num', 0.0, 100.0)  # band multiplier (std devs / ATR multiples)
_FR = ('num', 0.0, 1.0)     # 0..1 fraction (acceleration / volume factor)
_SCALE = ('num', 1e-9, 1e12)

INDICATORS = {
    # --- price (close) single-series -------------------------------------
    'sma': ('sma', ('close',), {'period': _W}, ('period',), None),
    'ema': ('ema', ('close',), {'period': _W}, ('period',), None),
    'rsi': ('rsi', ('close',), {'period': _W}, ('period',), None),
    'wma': ('wma', ('close',), {'period': _W}, ('period',), None),
    'hma': ('hma', ('close',), {'period': _W}, ('period',), None),
    'dema': ('dema', ('close',), {'period': _W}, ('period',), None),
    'tema': ('tema', ('close',), {'period': _W}, ('period',), None),
    'trima': ('trima', ('close',), {'period': _W}, ('period',), None),
    'zlema': ('zlema', ('close',), {'period': _W}, ('period',), None),
    'stdev': ('stdev', ('close',), {'period': _W}, ('period',), None),
    'kama': ('kama', ('close',), {'period': _W, 'fast': _S, 'slow': _S}, (), None),
    't3': ('t3', ('close',), {'period': _W, 'vfactor': _FR}, (), None),
    'roc': ('roc', ('close',), {'period': _W}, (), None),
    'momentum': ('momentum', ('close',), {'period': _W}, (), None),
    'trix': ('trix', ('close',), {'period': _W}, (), None),
    'cmo': ('cmo', ('close',), {'period': _W}, (), None),
    'dpo': ('dpo', ('close',), {'period': _W}, (), None),
    'coppock': ('coppock', ('close',), {'roc1': _S, 'roc2': _S, 'wma_period': _W}, (), None),
    'ulcer_index': ('ulcer_index', ('close',), {'period': _W}, (), None),
    'historical_volatility': ('historical_volatility', ('close',),
                              {'period': _W, 'annualize': _ANN}, (), None),
    # --- price (close) multi-output --------------------------------------
    'bollinger': ('bollinger', ('close',), {'period': _W, 'num_std': _MUL}, (),
                  ('mid', 'upper', 'lower')),
    'macd': ('macd', ('close',), {'fast': _S, 'slow': _S, 'signal': _S}, (),
             ('macd', 'signal', 'hist')),
    'ppo': ('ppo', ('close',), {'fast': _S, 'slow': _S, 'signal': _S}, (),
            ('ppo', 'signal', 'hist')),
    'stochrsi': ('stochrsi', ('close',),
                 {'rsi_period': _S, 'stoch_period': _S, 'smooth_k': _S, 'smooth_d': _S}, (),
                 ('stochrsi', 'k', 'd')),
    # --- OHLC single-series ----------------------------------------------
    'cci': ('cci', ('high', 'low', 'close'), {'period': _W}, (), None),
    'williams_r': ('williams_r', ('high', 'low', 'close'), {'period': _W}, (), None),
    'atr': ('atr', ('high', 'low', 'close'), {'period': _W}, (), None),
    'ultimate_oscillator': ('ultimate_oscillator', ('high', 'low', 'close'),
                            {'period1': _S, 'period2': _S, 'period3': _S}, (), None),
    'parabolic_sar': ('parabolic_sar', ('high', 'low'),
                      {'step': _FR, 'max_step': _FR}, (), None),
    'aroon_oscillator': ('aroon_oscillator', ('high', 'low'), {'period': _W}, (), None),
    'mass_index': ('mass_index', ('high', 'low'),
                   {'period': _W, 'ema_period': _S}, (), None),
    'awesome_oscillator': ('awesome_oscillator', ('high', 'low'),
                           {'fast': _S, 'slow': _S}, (), None),
    'fisher_transform': ('fisher_transform', ('high', 'low'), {'period': _W}, (), None),
    'chaikin_volatility': ('chaikin_volatility', ('high', 'low'), {'period': _W}, (), None),
    # --- OHLC multi-output -----------------------------------------------
    'stochastic': ('stochastic', ('high', 'low', 'close'),
                   {'period': _W, 'smooth_k': _S, 'smooth_d': _S}, (),
                   ('fast_k', 'k', 'd')),
    'donchian': ('donchian', ('high', 'low'), {'period': _W}, (),
                 ('upper', 'lower', 'mid')),
    'adx': ('adx', ('high', 'low', 'close'), {'period': _W}, (),
            ('plus_di', 'minus_di', 'adx')),
    'aroon': ('aroon', ('high', 'low'), {'period': _W}, (), ('up', 'down')),
    'vortex': ('vortex', ('high', 'low', 'close'), {'period': _W}, (),
               ('vi_plus', 'vi_minus')),
    'keltner': ('keltner', ('high', 'low', 'close'),
                {'period': _W, 'atr_period': _S, 'mult': _MUL}, (),
                ('mid', 'upper', 'lower')),
    'ichimoku': ('ichimoku', ('high', 'low', 'close'),
                 {'tenkan': _S, 'kijun': _S, 'senkou_b': _S}, (),
                 ('tenkan', 'kijun', 'senkou_a', 'senkou_b', 'chikou')),
    'pivot_points': ('pivot_points', ('high', 'low', 'close'), {}, (),
                     ('p', 'r1', 'r2', 'r3', 's1', 's2', 's3')),
    'elder_ray': ('elder_ray', ('high', 'low', 'close'), {'period': _W}, (),
                  ('bull', 'bear')),
    'rvi': ('rvi', ('open', 'high', 'low', 'close'), {'period': _W}, (),
            ('rvi', 'signal')),
    # --- volume based (require a 'volume' field on every bar) -------------
    'obv': ('obv', ('close', 'volume'), {}, (), None),
    'vwap': ('vwap', ('high', 'low', 'close', 'volume'), {}, (), None),
    'vwma': ('vwma', ('close', 'volume'), {'period': _W}, (), None),
    'mfi': ('mfi', ('high', 'low', 'close', 'volume'), {'period': _W}, (), None),
    'cmf': ('cmf', ('high', 'low', 'close', 'volume'), {'period': _W}, (), None),
    'force_index': ('force_index', ('close', 'volume'), {'period': _S}, (), None),
    'ease_of_movement': ('ease_of_movement', ('high', 'low', 'volume'),
                         {'period': _W, 'scale': _SCALE}, (), None),
    'ad_line': ('ad_line', ('high', 'low', 'close', 'volume'), {}, (), None),
    'chaikin_oscillator': ('chaikin_oscillator', ('high', 'low', 'close', 'volume'),
                           {'fast': _S, 'slow': _S}, (), None),
    'pvt': ('pvt', ('close', 'volume'), {}, (), None),
}

# Operands that expose both a fast and a slow lookback must keep fast < slow,
# otherwise the oscillator inverts its own sign. Only enforced when the caller
# pins both; omitting them keeps the standard (already valid) defaults.
_FAST_SLOW = frozenset({'macd', 'ppo', 'awesome_oscillator', 'chaikin_oscillator'})


class OperandCombinationError(ValueError):
    """A structurally valid operand whose parameter *combination* is rejected.

    A ValueError subclass so existing callers that catch ValueError are
    unaffected, but distinguishable so the parameter optimizer can skip an
    infeasible grid cell instead of aborting the whole sweep.
    """


def _check_param(value, spec):
    kind, lo, hi = spec
    if kind == 'int':
        return type(value) is int and lo <= value <= hi
    return type(value) in (int, float) and math.isfinite(value) and lo <= value <= hi


def _operand_key(operand):
    """Stable precompute/cache key: type + its present params (sorted) + component."""
    params = INDICATORS[operand['type']][2]
    return (operand['type'],
            tuple(sorted((k, operand[k]) for k in params if k in operand)),
            operand.get('component'))


def _validate_indicator_operand(value):
    """Validate an extended indicator operand against INDICATORS. Raises ValueError."""
    _fn, _inputs, params, required, components = INDICATORS[value['type']]
    allowed = {'type', *params}
    mandatory = {'type', *required}
    if components is not None:
        allowed.add('component')
        mandatory.add('component')
    keys = set(value)
    if not mandatory <= keys or not keys <= allowed:
        raise ValueError("invalid parameters for operand '%s'" % value['type'])
    for name, spec in params.items():
        if name in value and not _check_param(value[name], spec):
            raise ValueError("operand '%s' parameter '%s' is out of range" % (value['type'], name))
    if value['type'] in _FAST_SLOW and 'fast' in value and 'slow' in value and value['fast'] >= value['slow']:
        raise OperandCombinationError("operand '%s' requires fast < slow" % value['type'])
    if components is not None:
        component = value['component']
        if type(component) is not str or component not in components:
            raise ValueError("operand '%s' requires component in %s" % (value['type'], tuple(components)))


def operand_schema_oneof():
    """Build the published JSON-Schema `$defs.operand.oneOf` from INDICATORS.

    Single source of truth for the on-disk runtime/strategy.schema.json operand
    union; a test asserts the file matches this (no drift). field/constant keep
    their historical shape; every indicator contributes one object branch keyed
    by a distinct `type` const, so the union stays an exact oneOf.
    """
    branches = [
        {'type': 'object',
         'properties': {'type': {'const': 'field'},
                        'field': {'enum': ['open', 'high', 'low', 'close']}},
         'required': ['type', 'field'], 'additionalProperties': False},
        {'type': 'object',
         'properties': {'type': {'const': 'constant'},
                        'value': {'type': 'number', 'minimum': -1e12, 'maximum': 1e12}},
         'required': ['type', 'value'], 'additionalProperties': False},
    ]
    for name, (_fn, _inputs, params, required, components) in INDICATORS.items():
        properties = {'type': {'const': name}}
        for pname, (kind, lo, hi) in params.items():
            properties[pname] = {'type': 'integer' if kind == 'int' else 'number',
                                 'minimum': lo, 'maximum': hi}
        req = ['type', *required]
        if components is not None:
            properties['component'] = {'enum': list(components)}
            req.append('component')
        branches.append({'type': 'object', 'properties': properties,
                         'required': req, 'additionalProperties': False})
    return branches


def validate_definition(definition):
    if not isinstance(definition, dict):
        raise ValueError('strategy must be an object')
    try:
        serialized = json.dumps(definition, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('strategy must be finite JSON') from exc
    if len(serialized.encode()) > 100_000:
        raise ValueError('strategy exceeds size limit')
    d = copy.deepcopy(definition)
    if type(d.get('schemaVersion')) is not int or d['schemaVersion'] != 1:
        raise ValueError('unsupported schemaVersion')
    name = d.get('name')
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c)<32 for c in name):
        raise ValueError('name must contain 1..80 printable characters')
    if d.get('kind') == 'python':
        if set(d) != {'schemaVersion','kind','name','code'}:
            raise ValueError('unknown Python definition fields')
        from meritz.strategy_runner import validate_code
        result = validate_code(d['code'])
        if not result['valid']:
            raise StrategyValidationError(result['errors'])
        return d
    if d.get('kind') != 'dsl' or set(d)-{'schemaVersion','kind','name','entry','exit','quantity','allowPyramiding','risk'}:
        raise ValueError('unknown strategy kind or fields')
    count = 0
    def node(value, depth=1, operand=False):
        nonlocal count
        count += 1
        if count > 64 or depth > 8 or type(value) is not dict:
            raise ValueError('DSL exceeds depth/node limit or invalid node')
        if operand:
            t = value.get('type')
            if t == 'field' and set(value)=={'type','field'} and value['field'] in ('open','high','low','close'):
                return
            if t == 'constant' and set(value)=={'type','value'} and type(value['value']) in (int,float) and abs(value['value']) <= 1e12 and math.isfinite(value['value']):
                return
            if t in INDICATORS:
                _validate_indicator_operand(value)
                return
            raise ValueError('invalid operand')
        op = value.get('op')
        if op in ('all','any') and set(value)=={'op','conditions'} and type(value['conditions']) is list and 1 <= len(value['conditions']) <= 32:
            for child in value['conditions']: node(child,depth+1)
        elif op in ('gt','gte','lt','lte','cross_above','cross_below') and set(value)=={'op','left','right'}:
            node(value['left'],depth+1,True); node(value['right'],depth+1,True)
        else:
            raise ValueError('invalid condition')
    node(d.get('entry')); node(d.get('exit'))
    d.setdefault('quantity',None); d.setdefault('allowPyramiding',False)
    if d['quantity'] is not None and (type(d['quantity']) is not int or not 1<=d['quantity']<=1_000_000_000):
        raise ValueError('quantity must be null or positive integer')
    if type(d['allowPyramiding']) is not bool:
        raise ValueError('allowPyramiding must be boolean')
    risk=d.setdefault('risk',{})
    if type(risk) is not dict or set(risk)-{'stopLossPct','takeProfitPct'}:
        raise ValueError('invalid risk policy')
    for key in ('stopLossPct','takeProfitPct'):
        risk.setdefault(key,None)
        v=risk[key]
        if v is not None and (type(v) not in (int,float) or not 0<v<=100 or not math.isfinite(v)):
            raise ValueError('risk percentage must be null or 0..100')
    return d


def _series(history, field):
    """Pull one bar field as a past->present list. Missing volume is a clear error."""
    try:
        return [bar[field] for bar in history]
    except KeyError:
        raise ValueError("indicator operand requires a '%s' field on every bar" % field)


def _value(node, history):
    t=node['type']
    if t=='constant': return node['value']
    if not history: return None
    if t=='field': return history[-1][node['field']]
    if t in ('sma','ema','rsi'):
        period=node['period']; values=[b['close'] for b in history]
        if len(values)<period+(t=='rsi'): return None
        # Canonical SDK formulas: EMA uses SMA seeding; Wilder RSI is 100
        # when average loss is zero, including a completely flat price series.
        # Keep undefined warmup explicit before selecting the last computed value.
        return {'sma': calc.sma, 'ema': calc.ema, 'rsi': calc.rsi}[t](values, period)[-1]
    # Extended indicators resolve causally on the current prefix: computing on
    # history[:k] and taking [-1] equals computing on the full series at index k-1.
    fn, inputs, params, _required, components = INDICATORS[t]
    args = [_series(history, field) for field in inputs]
    kwargs = {name: node[name] for name in params if name in node}
    result = getattr(calc, fn)(*args, **kwargs)
    if components is not None:
        result = result[node['component']]
    return result[-1] if result else None



def make_decider(definition, bars=None):
    d=validate_definition(definition)
    if d['kind']!='dsl': raise ValueError('Python requires isolated StrategySession')
    # Precompute every indicator output series once for a trusted immutable dataset.
    # Each indicator is causal, so the full-series value at index k equals
    # evaluating it on the prefix history[:k+1] and taking [-1]; evaluation only
    # ever selects the current/previous prefix index, never a future value. This
    # turns an O(n^2) per-bar recompute into O(n) regardless of indicator count.
    series = {}
    if bars is not None:
        raw = {}
        def precompute(operand):
            kind = operand['type']
            if kind not in INDICATORS:
                return
            key = _operand_key(operand)
            if key in series:
                return
            fn, inputs, params, _req, components = INDICATORS[kind]
            shape = (kind, tuple(sorted((k, operand[k]) for k in params if k in operand)))
            if shape not in raw:
                raw[shape] = getattr(calc, fn)(*[_series(bars, f) for f in inputs],
                                               **{k: operand[k] for k in params if k in operand})
            result = raw[shape]
            series[key] = result[operand['component']] if components is not None else result
        def collect(node):
            if node['op'] in ('all', 'any'):
                for child in node['conditions']: collect(child)
            else:
                precompute(node['left']); precompute(node['right'])
        collect(d['entry']); collect(d['exit'])
    def value(node, history):
        if bars is not None and node['type'] in INDICATORS:
            index = len(history) - 1
            return None if index < 0 else series[_operand_key(node)][index]
        return _value(node, history)
    def decide(ctx):
        history=ctx['history'][:ctx['index']+1]
        trace=[]
        def check(n,path):
            op=n['op']
            if op in ('all','any'):
                children=[check(c,f'{path}.{i}') for i,c in enumerate(n['conditions'])]
                result=all(children) if op=='all' else any(children)
                trace.append({'node':path,'op':op,'result':result}); return result
            left,right=value(n['left'],history),value(n['right'],history)
            prev_left=prev_right=None; result=False
            if left is not None and right is not None:
                if op.startswith('cross_'):
                    prev_left,prev_right=value(n['left'],history[:-1]),value(n['right'],history[:-1])
                    if prev_left is not None and prev_right is not None:
                        result=(prev_left<=prev_right and left>right) if op=='cross_above' else (prev_left>=prev_right and left<right)
                else: result={'gt':left>right,'gte':left>=right,'lt':left<right,'lte':left<=right}[op]
            trace.append({'node':path,'op':op,'left':left,'right':right,'previousLeft':prev_left,'previousRight':prev_right,'result':result})
            return result
        entry,exit_=check(d['entry'],'entry'),check(d['exit'],'exit')
        portfolio=ctx['portfolio']; held=portfolio['shares']>0
        average=portfolio.get('averageCost',0); close=history[-1]['close']
        stop=d['risk']['stopLossPct']; take=d['risk']['takeProfitPct']
        stop_hit=bool(held and average>0 and stop is not None and close<=average*(1-stop/100))
        take_hit=bool(held and average>0 and take is not None and close>=average*(1+take/100))
        action='sell' if held and (exit_ or stop_hit or take_hit) else ('buy' if entry and (not held or d['allowPyramiding']) else 'hold')
        result={'action':action,'reason':'DSL rule evaluation','evidence':{'origin':'dsl-evaluated','barIndex':ctx['index'],'rules':trace,'stopLoss':stop_hit,'takeProfit':take_hit}}
        if action!='hold' and d['quantity'] is not None: result['quantity']=d['quantity']
        return result
    return decide


def templates():
    root=Path(__file__).resolve().parent.parent/'strategies'
    # Keep the original default first even as the library grows.
    paths = sorted(root.glob('*.json'), key=lambda p: (p.name != 'sma-cross.json', p.name))
    return [validate_definition(json.loads(p.read_text(encoding='utf-8'))) for p in paths]


def template_details(definitions):
    """Display text lives separately from executable definitions."""
    import tomllib
    path = Path(__file__).resolve().parent.parent / 'strategies/template-details.toml'
    rows = tomllib.loads(path.read_text(encoding='utf-8'))['templates']
    by_name = {row['name']: row for row in rows}
    if len(by_name) != len(rows) or set(by_name) != {d['name'] for d in definitions}:
        raise ValueError('Template descriptions must match the strategy library exactly')
    return [copy.deepcopy(by_name[d['name']]) for d in definitions]


def strategy_schema():
    return json.loads(Path(__file__).with_name('strategy.schema.json').read_text(encoding='utf-8'))
