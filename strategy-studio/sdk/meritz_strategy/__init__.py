"""Pure strategy helpers. No I/O, credentials, broker implementation or live orders.

Use Context(ctx) inside on_bar; return ctx.buy()/sell()/close_position() intent.
initialize(ctx) may establish caller-owned strategy state before the first bar.
"""
from __future__ import annotations
import copy
import math
from indicators import calc

__version__ = '0.1.0'

# Mirror of runtime.dsl.INDICATORS so the SDK exposes the same indicator library
# as the strategy DSL. Kept self-contained because the SDK ships as a standalone
# wheel (see packaging/check_sdk_wheel.py) and must not import the runtime.
# Tuple layout: (calc_fn, inputs, params{name:(kind,lo,hi)}, required, components).
_W = ('int', 2, 250)
_S = ('int', 1, 250)
_ANN = ('int', 1, 100_000)
_MUL = ('num', 0.0, 100.0)
_FR = ('num', 0.0, 1.0)
_SCALE = ('num', 1e-9, 1e12)

_INDICATORS = {
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
    'bollinger': ('bollinger', ('close',), {'period': _W, 'num_std': _MUL}, (),
                  ('mid', 'upper', 'lower')),
    'macd': ('macd', ('close',), {'fast': _S, 'slow': _S, 'signal': _S}, (),
             ('macd', 'signal', 'hist')),
    'ppo': ('ppo', ('close',), {'fast': _S, 'slow': _S, 'signal': _S}, (),
            ('ppo', 'signal', 'hist')),
    'stochrsi': ('stochrsi', ('close',),
                 {'rsi_period': _S, 'stoch_period': _S, 'smooth_k': _S, 'smooth_d': _S}, (),
                 ('stochrsi', 'k', 'd')),
    'cci': ('cci', ('high', 'low', 'close'), {'period': _W}, (), None),
    'williams_r': ('williams_r', ('high', 'low', 'close'), {'period': _W}, (), None),
    'atr': ('atr', ('high', 'low', 'close'), {'period': _W}, (), None),
    'ultimate_oscillator': ('ultimate_oscillator', ('high', 'low', 'close'),
                            {'period1': _S, 'period2': _S, 'period3': _S}, (), None),
    'parabolic_sar': ('parabolic_sar', ('high', 'low'), {'step': _FR, 'max_step': _FR}, (), None),
    'aroon_oscillator': ('aroon_oscillator', ('high', 'low'), {'period': _W}, (), None),
    'mass_index': ('mass_index', ('high', 'low'), {'period': _W, 'ema_period': _S}, (), None),
    'awesome_oscillator': ('awesome_oscillator', ('high', 'low'), {'fast': _S, 'slow': _S}, (), None),
    'fisher_transform': ('fisher_transform', ('high', 'low'), {'period': _W}, (), None),
    'chaikin_volatility': ('chaikin_volatility', ('high', 'low'), {'period': _W}, (), None),
    'stochastic': ('stochastic', ('high', 'low', 'close'),
                   {'period': _W, 'smooth_k': _S, 'smooth_d': _S}, (), ('fast_k', 'k', 'd')),
    'donchian': ('donchian', ('high', 'low'), {'period': _W}, (), ('upper', 'lower', 'mid')),
    'adx': ('adx', ('high', 'low', 'close'), {'period': _W}, (), ('plus_di', 'minus_di', 'adx')),
    'aroon': ('aroon', ('high', 'low'), {'period': _W}, (), ('up', 'down')),
    'vortex': ('vortex', ('high', 'low', 'close'), {'period': _W}, (), ('vi_plus', 'vi_minus')),
    'keltner': ('keltner', ('high', 'low', 'close'),
                {'period': _W, 'atr_period': _S, 'mult': _MUL}, (), ('mid', 'upper', 'lower')),
    'ichimoku': ('ichimoku', ('high', 'low', 'close'),
                 {'tenkan': _S, 'kijun': _S, 'senkou_b': _S}, (),
                 ('tenkan', 'kijun', 'senkou_a', 'senkou_b', 'chikou')),
    'pivot_points': ('pivot_points', ('high', 'low', 'close'), {}, (),
                     ('p', 'r1', 'r2', 'r3', 's1', 's2', 's3')),
    'elder_ray': ('elder_ray', ('high', 'low', 'close'), {'period': _W}, (), ('bull', 'bear')),
    'rvi': ('rvi', ('open', 'high', 'low', 'close'), {'period': _W}, (), ('rvi', 'signal')),
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


# Indicators exposing both fast and slow lookbacks require fast < slow.
_FAST_SLOW = frozenset({'macd', 'ppo', 'awesome_oscillator', 'chaikin_oscillator'})


def _sdk_param_ok(value, spec):
    kind, lo, hi = spec
    if kind == 'int':
        return type(value) is int and lo <= value <= hi
    return type(value) in (int, float) and math.isfinite(value) and lo <= value <= hi


def _sdk_series(history, field):
    try:
        return [bar[field] for bar in history]
    except KeyError:
        raise ValueError("indicator requires a '%s' field on every bar" % field)


class Context:
    def __init__(self, observation):
        self._observation = observation
        self.bar = observation['bar']
        self.history = observation['history']
        self.index = observation['index']
        self.date = observation['date']

    def get_cash(self):
        return self._observation['portfolio']['cash']

    def get_position(self):
        p = self._observation['portfolio']
        return {'quantity':p['shares'],'averageCost':p.get('averageCost',0),'unrealizedPnl':p.get('unrealizedPnl',0)}

    def get_portfolio(self):
        return copy.deepcopy(self._observation['portfolio'])

    @staticmethod
    def _intent(action, quantity=None, *, reason='', order_type='market', limit_price=None, evidence=None):
        intent = {'action':action,'reason':reason,'orderType':order_type}
        if quantity is not None: intent['quantity']=quantity
        if limit_price is not None: intent['limitPrice']=limit_price
        if evidence is not None: intent['evidence']=evidence
        return intent

    def buy(self, quantity=None, **options):
        return self._intent('buy',quantity,**options)

    def sell(self, quantity=None, **options):
        return self._intent('sell',quantity,**options)

    def close_position(self, **options):
        return self.sell(**options) if self.get_position()['quantity'] else {'action':'hold'}

    def get_indicator(self, name, period=None, field='close', component=None, **params):
        """Last completed-bar indicator value; None during warmup. No future rows.

        sma/ema/rsi keep their original call shape — get_indicator('sma', 20). The
        extended library (see _INDICATORS) is reached by name, with multi-output
        indicators selected via component=, e.g.
        get_indicator('macd', component='signal') or
        get_indicator('bollinger', component='upper', period=20, num_std=2).
        For single-price indicators (close-series) `field` picks the price series;
        OHLC/volume indicators read their own bar fields and ignore `field`.
        Volume indicators need a 'volume' field on every bar or they raise.

        `period` is passed through only when given, so an omitted period uses the
        same calc.py default the DSL uses (e.g. cci->20, atr->14) rather than an
        SDK-specific override; this keeps SDK and DSL signals identical.
        """
        if field not in ('open','high','low','close'):
            raise ValueError('field must be OHLC')
        name=name.lower()
        if name in ('sma','ema','rsi'):
            resolved = 20 if period is None else period     # historical SDK default
            if type(resolved) is not int or not 2 <= resolved <= 250:
                raise ValueError('period must be 2..250 and field OHLC')
            if component is not None or params:
                raise ValueError('sma/ema/rsi accept only period and field')
            values=[bar[field] for bar in self.history]
            if len(values)<resolved+(name=='rsi'): return None
            return getattr(calc, name)(values, resolved)[-1]
        if name not in _INDICATORS:
            raise ValueError('supported indicators: ' + ', '.join(sorted(_INDICATORS)))
        fn, inputs, pspec, required, components = _INDICATORS[name]
        # The convenience `period` argument feeds a period-taking indicator only
        # when explicitly supplied; otherwise the calc default applies (DSL parity).
        if period is not None and 'period' in pspec and 'period' not in params:
            params = {**params, 'period': period}
        unknown = set(params) - set(pspec)
        if unknown:
            raise ValueError('unknown parameters for %s: %s' % (name, ', '.join(sorted(unknown))))
        for key in required:
            if key not in params:
                raise ValueError("operand '%s' requires parameter '%s'" % (name, key))
        for key, value in params.items():
            if not _sdk_param_ok(value, pspec[key]):
                raise ValueError("operand '%s' parameter '%s' is out of range" % (name, key))
        if name in _FAST_SLOW and 'fast' in params and 'slow' in params and params['fast'] >= params['slow']:
            raise ValueError("operand '%s' requires fast < slow" % name)
        if components is None:
            if component is not None:
                raise ValueError("indicator '%s' has no components" % name)
        elif type(component) is not str or component not in components:
            raise ValueError("indicator '%s' requires component in %s" % (name, tuple(components)))
        if inputs == ('close',):
            args = [_sdk_series(self.history, field)]
        else:
            args = [_sdk_series(self.history, col) for col in inputs]
        result = getattr(calc, fn)(*args, **params)
        if components is not None:
            result = result[component]
        return result[-1] if result else None
