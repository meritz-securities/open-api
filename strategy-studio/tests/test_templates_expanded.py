"""Coverage for the expanded template library (Claude-authored).

Every template that is NOT one of the original 8 is loaded from strategies/*.json,
validated against the closed DSL, and run through the real decider on a synthetic
trending+ranging OHLC series. Each must produce at least one buy and one sell
decision (so no template is dead), and must have a matching template-details.toml
entry. No broker, credentials or network are involved.
"""
import json
import math
import tomllib
from pathlib import Path

import pytest

from runtime.dsl import make_decider, validate_definition, templates, template_details

ROOT = Path(__file__).resolve().parents[1]
STRAT = ROOT / 'strategies'

# The library the project shipped before the expansion. Everything else is new.
ORIGINAL_FILES = {
    'buy-hold.json', 'ema-cross.json', 'price-sma-cross.json', 'rsi-accumulate.json',
    'rsi-recovery.json', 'sma-cross.json', 'sma-long-cross.json', 'sma-rsi-filter.json',
}

NEW_FILES = sorted(p.name for p in STRAT.glob('*.json') if p.name not in ORIGINAL_FILES)


def new_definition(fname):
    return json.loads((STRAT / fname).read_text(encoding='utf-8'))


def synthetic_bars(n=460):
    """Deterministic OHLC, past->present: up-trend, down-trend, range, up-trend,
    all with layered oscillations so oscillator/band/cross signals actually fire."""
    vals = []
    for i in range(n):
        if i < 130:
            trend = 100 + i * 0.55
        elif i < 250:
            trend = 100 + 130 * 0.55 - (i - 130) * 0.7
        elif i < 330:
            trend = 100 + 130 * 0.55 - 120 * 0.7           # ranging plateau
        else:
            trend = 100 + 130 * 0.55 - 120 * 0.7 + (i - 330) * 0.45
        osc = 9 * math.sin(i / 7.0) + 4 * math.sin(i / 2.3) + 2.5 * math.sin(i / 1.3)
        vals.append(max(5.0, trend + osc))
    bars = []
    prev = vals[0]
    for i, c in enumerate(vals):
        o = prev
        hi = max(o, c) + 1.8 + abs(math.sin(i / 3.0))
        lo = min(o, c) - 1.8 - abs(math.cos(i / 3.0))
        bars.append({'open': round(o, 4), 'high': round(hi, 4),
                     'low': round(lo, 4), 'close': round(c, 4)})
        prev = c
    return bars


def run_with_holding(definition, bars):
    """Drive the real decider, simulating a single long position so that both the
    entry and the exit branch are actually exercised. Returns (buys, sells)."""
    decide = make_decider(definition)
    held = False
    avg = 0.0
    buys = sells = 0
    for i in range(len(bars)):
        ctx = {'index': i, 'history': bars[:i + 1],
               'portfolio': {'shares': 100 if held else 0, 'averageCost': avg}}
        result = decide(ctx)
        assert result['action'] in ('buy', 'sell', 'hold')
        if result['action'] == 'buy' and not held:
            held, avg, buys = True, bars[i]['close'], buys + 1
        elif result['action'] == 'sell' and held:
            held, sells = False, sells + 1
    return buys, sells


# --------------------------------------------------------------------------- #
# there really is an expanded library
# --------------------------------------------------------------------------- #
def test_library_grew_beyond_the_original_eight():
    assert ORIGINAL_FILES <= {p.name for p in STRAT.glob('*.json')}
    assert len(NEW_FILES) >= 20                      # a rich set, not a token few
    total = len(list(STRAT.glob('*.json')))
    assert total == len(ORIGINAL_FILES) + len(NEW_FILES)
    assert total >= 28


@pytest.mark.parametrize('fname', NEW_FILES)
def test_new_template_validates(fname):
    definition = new_definition(fname)
    normalized = validate_definition(definition)
    assert normalized == definition                  # already in canonical form on disk
    assert definition['kind'] == 'dsl'
    assert definition['risk'] == {'stopLossPct': None, 'takeProfitPct': None}


@pytest.mark.parametrize('fname', NEW_FILES)
def test_new_template_runs_and_is_not_dead(fname):
    definition = new_definition(fname)
    bars = synthetic_bars()
    buys, sells = run_with_holding(definition, bars)
    assert buys > 0, f'{fname} never produced a buy decision'
    assert sells > 0, f'{fname} never produced a sell decision'


def test_cached_precompute_path_also_runs_each_new_template():
    # make_decider(bars=...) precomputes sma/ema/rsi; the new operands must still
    # resolve through the per-bar path without error and never return a nan.
    bars = synthetic_bars()
    for fname in NEW_FILES:
        definition = new_definition(fname)
        decide = make_decider(definition, bars=bars)
        for index in range(len(bars)):
            ctx = {'index': index, 'history': bars[:index + 1],
                   'portfolio': {'shares': 0, 'averageCost': 0}}
            assert decide(ctx)['action'] in ('buy', 'sell', 'hold')


# --------------------------------------------------------------------------- #
# every new template has a matching, well-formed template-details.toml entry
# --------------------------------------------------------------------------- #
def test_every_new_template_has_matching_details_entry():
    rows = tomllib.loads((STRAT / 'template-details.toml').read_text(encoding='utf-8'))['templates']
    by_name = {r['name']: r for r in rows}
    assert len(by_name) == len(rows)                 # no duplicate names
    for fname in NEW_FILES:
        name = new_definition(fname)['name']
        assert name in by_name, f'{fname} ({name}) has no template-details entry'
        info = by_name[name]
        assert set(info) == {'name', 'summary', 'buyRule', 'sellRule', 'sizing', 'cautions', 'parameters'}
        assert all(isinstance(info[k], str) and info[k] for k in ('summary', 'buyRule', 'sellRule', 'sizing'))
        assert info['cautions'] and all(isinstance(c, str) and c for c in info['cautions'])
        # standard non-advice disclaimer is always present
        assert any('투자 추천이나 수익 보장이 아닙니다' in c for c in info['cautions'])
        # no internal/implementation vocabulary leaks to users
        assert not any(w in json.dumps(info, ensure_ascii=False) for w in ('DSL', '리비전', 'hash', '계약'))


@pytest.mark.parametrize('fname', NEW_FILES)
def test_new_template_parameters_point_at_real_paths(fname):
    definition = new_definition(fname)
    rows = tomllib.loads((STRAT / 'template-details.toml').read_text(encoding='utf-8'))['templates']
    info = next(r for r in rows if r['name'] == definition['name'])
    assert info['parameters'], f'{fname} exposes no tunable parameters'
    for param in info['parameters']:
        assert set(param) == {'path', 'label', 'value'}
        actual = definition
        for piece in param['path'].split('.'):
            actual = actual[int(piece)] if isinstance(actual, list) else actual[piece]
        assert actual == param['value'], f'{fname}: {param["path"]} -> {actual!r} != {param["value"]!r}'


# --------------------------------------------------------------------------- #
# the whole bundle (originals + new) still loads through the real contract
# --------------------------------------------------------------------------- #
def test_full_bundle_definitions_and_details_stay_consistent():
    definitions = templates()
    details = template_details(definitions)             # raises unless names match exactly
    assert len(definitions) == len(details)
    assert {d['name'] for d in definitions} == {r['name'] for r in details}
    assert [d['name'] for d in definitions] == [r['name'] for r in details]
