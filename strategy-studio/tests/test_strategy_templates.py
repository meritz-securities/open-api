"""Template behavior checked against hand-specified rules and independent math.

No broker, credentials or network. Indicator oracle uses direct weighted sums,
not the production indicator helpers, and rules do not interpret template JSON.
"""
import copy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import pytest

from runtime.dsl import templates, validate_definition
from runtime.service import StrategyRuntime

ROOT = Path(__file__).resolve().parents[1]
NAMES = ['SMA 5/20 교차', '한 번 매수하고 보유', 'EMA 12/26 교차', '종가와 20일 평균 교차',
         '낮은 RSI에서 매 봉 1주', 'RSI 30 회복 · 70 이탈', 'SMA 20/60 교차', 'SMA 교차 + RSI 확인']
SETTINGS = {'initialCash': 100000, 'feeBps': 0, 'slippageBps': 0, 'taxBps': 0}


# Abrupt rebounds exercise the SMA+RSI filter accepting a crossing, while
# the long slopes exercise RSI accumulation and both downward exit signals.
def prices():
    return ([100 + i / 4 for i in range(65)] + [116 - i for i in range(1, 41)]
            + [96 + i for i in range(1, 81)] + [176 - i for i in range(1, 81)]
            + [116 + i for i in range(1, 51)])


def bars(values):
    return [{'date': (dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat(),
             'open': p, 'high': p + 1, 'low': p - 1, 'close': p} for i, p in enumerate(values)]


def mean_series(values, period):
    return [None if i < period - 1 else math.fsum(values[i-period+1:i+1])/period for i in range(len(values))]


def exponential_series(values, period):
    # Closed weighted sum rather than the production rolling recurrence.
    alpha = 2 / (period + 1)
    seed = math.fsum(values[:period]) / period
    return [None if i < period-1 else seed*(1-alpha)**(i-period+1)
            + math.fsum(alpha*values[j]*(1-alpha)**(i-j) for j in range(period, i+1))
            for i in range(len(values))]


def relative_strength(values, period=14):
    changes = [values[i]-values[i-1] for i in range(1, len(values))]
    gains = [max(v, 0) for v in changes]; losses = [max(-v, 0) for v in changes]
    def average(xs, i):
        return math.fsum(xs[:period])/period*((period-1)/period)**(i-period) + math.fsum(
            xs[j-1]/period*((period-1)/period)**(i-j) for j in range(period+1, i+1))
    result = [None]*period
    for i in range(period, len(values)):
        gain, loss = average(gains, i), average(losses, i)
        result.append(100.0 if loss == 0 else 100 - 100/(1+gain/loss))
    return result


def signals(name, values, fast=5, slow=20):
    short, long = mean_series(values, fast), mean_series(values, slow)
    rsi = relative_strength(values)
    if name == 'EMA 12/26 교차': short, long = exponential_series(values, 12), exponential_series(values, 26)
    elif name == 'SMA 20/60 교차': short, long = mean_series(values, 20), mean_series(values, 60)
    elif name == '종가와 20일 평균 교차': short, long = values, mean_series(values, 20)
    def cross(left, right, i, up):
        if i == 0 or any(v is None for v in (left[i-1], right[i-1], left[i], right[i])): return False
        return left[i-1] <= right[i-1] and left[i] > right[i] if up else left[i-1] >= right[i-1] and left[i] < right[i]
    buy, sell = [], []
    for i in range(len(values)):
        if name == '한 번 매수하고 보유': a, b = True, False
        elif name == '낮은 RSI에서 매 봉 1주': a, b = rsi[i] is not None and rsi[i] < 30, rsi[i] is not None and rsi[i] > 70
        elif name == 'RSI 30 회복 · 70 이탈': a, b = cross(rsi, [30]*len(values), i, True), cross(rsi, [70]*len(values), i, False)
        else:
            a, b = cross(short, long, i, True), cross(short, long, i, False)
            if name == 'SMA 교차 + RSI 확인': a = a and rsi[i] is not None and rsi[i] > 50
        buy.append(a); sell.append(b)
    return buy, sell


def expected_actions_and_trades(name, values, quantity=None, fast=5, slow=20):
    buy, sell = signals(name, values, fast, slow)
    accumulating = name == '낮은 RSI에서 매 봉 1주'
    cash, shares, pending = 100000., 0, None
    actions, trades = [], []
    for i, price in enumerate(values):
        if pending:
            available = int(cash // price) if pending == 'buy' else shares
            qty = available if quantity is None else quantity
            assert 0 < qty <= available  # The fixture intentionally avoids rejections.
            cash += qty * price * (-1 if pending == 'buy' else 1)
            shares += qty * (1 if pending == 'buy' else -1)
            trades.append((i, pending.upper(), qty))
        action = 'sell' if shares and sell[i] else 'buy' if buy[i] and (not shares or accumulating) else 'hold'
        actions.append(action); pending = None if action == 'hold' else action
    return actions, trades


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import requests
    monkeypatch.setenv('MERITZ_NO_ENV_FILE', '1')
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **k: pytest.fail('Template test attempted network access'))


def test_default_original_and_description_contract(tmp_path):
    bundle = StrategyRuntime(tmp_path).templates()
    names = [d['name'] for d in bundle['templates']]
    # The library may grow beyond the original 8; the 8 originals must stay present in their
    # original relative order (default first), and details must align 1:1 with templates.
    assert set(NAMES) <= set(names)
    assert [n for n in names if n in NAMES] == NAMES
    original_path = ROOT/'strategies/sma-cross.json'
    assert hashlib.sha256(original_path.read_bytes()).hexdigest() == '66eb2c9cbf02c72cf156e72f0331dc0331e09dd55b957c649a6b45097cfa35a3'
    original = json.loads(original_path.read_text())
    assert bundle['templates'][0] == original
    assert original['quantity'] is None and original['allowPyramiding'] is False
    assert [r['name'] for r in bundle['templateDetails']] == names
    for definition, info in zip(bundle['templates'], bundle['templateDetails']):
        assert validate_definition(definition) == definition
        assert set(info) == {'name','summary','buyRule','sellRule','sizing','cautions','parameters'}
        assert all(isinstance(info[k], str) and info[k] for k in ('name','summary','buyRule','sellRule','sizing'))
        assert info['cautions'] and all(isinstance(item,str) for item in info['cautions'])
        assert not any(word in json.dumps(info,ensure_ascii=False) for word in ('DSL','리비전','hash','계약'))
        assert definition['risk'] == {'stopLossPct':None,'takeProfitPct':None}
        for param in info['parameters']:
            assert set(param) == {'path','label','value'}
            actual = definition
            for piece in param['path'].split('.'): actual = actual[int(piece)] if isinstance(actual,list) else actual[piece]
            assert actual == param['value']
    accumulate = next(r for r in bundle['templateDetails'] if r['name'] == '낮은 RSI에서 매 봉 1주')
    assert '정기 적립이 아닙니다' in ' '.join(accumulate['cautions'])
    assert '손절·익절을 추가해도' in ' '.join(accumulate['cautions'])


@pytest.mark.parametrize('name', NAMES)
def test_every_template_runtime_signals_and_next_bar_trades_match_independent_oracle(tmp_path, name):
    service = StrategyRuntime(tmp_path)
    definition = next(d for d in templates() if d['name'] == name)
    values = prices(); data = service.import_data(bars(values), {'kind':'imported','label':'invented oracle prices','currency':'KRW'})
    revision = service.save_strategy(definition)
    run = service.run(revision['id'], data['id'], SETTINGS)
    assert run['status'] == 'succeeded'
    expected_actions, expected_trades = expected_actions_and_trades(name, values, definition['quantity'])
    assert [d['action'] for d in run['report']['decisions']] == expected_actions
    buy_signals, sell_signals = signals(name, values)
    for index, decision in enumerate(run['report']['decisions']):
        rules = decision['evidence']['details']['rules']
        assert next(r['result'] for r in rules if r['node'] == 'entry') == buy_signals[index]
        assert next(r['result'] for r in rules if r['node'] == 'exit') == sell_signals[index]
    dates = [b['date'] for b in bars(values)]
    actual = [(dates.index(t['date']), t['side'], t['quantity']) for t in run['report']['trades']]
    assert actual == expected_trades and actual
    for trade in run['report']['trades']:
        assert dates.index(trade['date']) == dates.index(trade['signalDate']) + 1
    if name == '한 번 매수하고 보유':
        assert len(actual) == 1 and actual[0][0] == 1
        assert run['report']['finalPortfolio']['shares'] > 0
    elif name == '낮은 RSI에서 매 봉 1주':
        buys = [i for i, side, qty in actual if side == 'BUY']
        assert any(b == a+1 for a,b in zip(buys, buys[1:]))
        assert all(qty == 1 for _, _, qty in actual)
    else:
        assert any(side == 'SELL' for _, side, _ in actual)


def test_representative_period_quantity_and_cost_edits_leave_template_and_parent_unchanged(tmp_path):
    service = StrategyRuntime(tmp_path)
    before = service.templates()
    file_hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'strategies').iterdir() if p.is_file()}
    parent = service.save_strategy(before['templates'][0])
    edited = copy.deepcopy(parent['definition']); edited['name'] = '내 조건 3/10 · 2주'
    for side in ('entry','exit'):
        edited[side]['left']['period'] = 3; edited[side]['right']['period'] = 10
    edited['quantity'] = 2
    child = service.save_strategy(edited, parent['id'])
    data = service.import_data(bars(prices()), {'kind':'imported','label':'settings oracle'})
    run = service.run(child['id'], data['id'], SETTINGS)
    actions, trades = expected_actions_and_trades('SMA 5/20 교차', prices(), 2, fast=3, slow=10)
    assert [d['action'] for d in run['report']['decisions']] == actions
    assert [(t['side'],t['quantity']) for t in run['report']['trades']] == [(side,qty) for _,side,qty in trades]
    cost_run = service.run(child['id'], data['id'], {**SETTINGS,'feeBps':10})
    assert cost_run['report']['metrics']['totalFees'] > 0
    assert cost_run['report']['metrics']['finalEquity'] < run['report']['metrics']['finalEquity']
    assert service.get_strategy(parent['id']) == parent
    assert child['parentRevisionId'] == parent['id'] and child['hash'] != parent['hash']
    before['templates'][0]['name'] = 'caller mutation'
    before['templateDetails'][0]['summary'] = 'caller mutation'
    assert service.templates()['templates'][0]['name'] == NAMES[0]
    assert service.templates()['templateDetails'][0]['summary'] != 'caller mutation'
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'strategies').iterdir() if p.is_file()} == file_hashes


def test_rsi_filter_rejects_a_real_sma_cross_and_does_not_buy_late(tmp_path):
    # Explicit non-engine fixture: after the prior rally, a drop to 146 and 26
    # flat bars bring both averages together; small rises cross SMA while the
    # independently computed Wilder RSI remains below the filter threshold.
    values = prices() + [146]*26 + [146.5, 147, 147.5]
    raw_buy, _ = signals('SMA 5/20 교차', values)
    filtered_buy, _ = signals('SMA 교차 + RSI 확인', values)
    rejected = [i for i, (raw, filtered) in enumerate(zip(raw_buy, filtered_buy)) if raw and not filtered]
    assert rejected == [341]
    assert sum(raw_buy) == 3 and sum(filtered_buy) == 2
    rsi = relative_strength(values)
    assert 44 < rsi[341] < 46  # Reliably below 50, not a rounding-boundary case.
    assert any(value > 50 for value in rsi[342:])
    assert not any(filtered_buy[341:])
    service = StrategyRuntime(tmp_path)
    data = service.import_data(bars(values), {'kind':'imported','label':'invented RSI rejection fixture'})
    runs = {}
    for name in ('SMA 5/20 교차', 'SMA 교차 + RSI 확인'):
        definition = next(d for d in templates() if d['name'] == name)
        revision = service.save_strategy(definition)
        run = service.run(revision['id'], data['id'], SETTINGS)
        assert run['status'] == 'succeeded'
        expected, _ = expected_actions_and_trades(name, values)
        assert [d['action'] for d in run['report']['decisions']] == expected
        runs[name] = run
    raw = runs['SMA 5/20 교차']['report']; filtered = runs['SMA 교차 + RSI 확인']['report']
    assert raw['decisions'][341]['action'] == 'buy'
    assert all(decision['action'] == 'hold' for decision in filtered['decisions'][341:])
    evidence = {rule['node']:rule for rule in filtered['decisions'][341]['evidence']['details']['rules']}
    assert evidence['entry.0']['result'] is True
    assert evidence['entry.1']['result'] is False
    assert evidence['entry']['result'] is False
    signal_date = bars(values)[341]['date']
    assert any(t['signalDate'] == signal_date and t['side'] == 'BUY' for t in raw['trades'])
    assert not any(t['signalDate'] == signal_date for t in filtered['trades'])
