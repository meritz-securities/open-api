"""Hand-checked tests for the additive descriptive metrics (doctor-v1).

These cover ONLY the metrics added on top of the pre-existing analytics output
(CAGR, sharpe, sortino, maxDrawdownPct, winRatePct, profitFactor, realized/
unrealized PnL, monthlyReturns). Each expected value below is computed by hand
from a small synthetic equity/PnL series; see the inline arithmetic.
"""
import json
import math
from datetime import date

import pytest

from analytics import analyze


def synthetic_report():
    # Equity: 100 -> 120 -> 90 -> 110 -> 130 -> 150 over six monthly bars.
    #   per-bar returns: +20%, -25%, +22.22%, +18.18%, +15.38%
    #   drawdown: peak 120 at bar2, trough 90 at bar3 -> MDD = (120-90)/120 = 25%
    #   net return vs initial 100: (150/100 - 1) = +50%
    # Sell fills realized PnL in trade order: 20, 10, -5, -15, 30
    #   wins [20,10,30] gains=60 count=3 -> avgWin=20
    #   losses [-5,-15] magnitude=20 count=2 -> avgLoss=10
    #   winLossRatio=20/10=2 ; expectancy=(20+10-5-15+30)/5=40/5=8
    #   streaks (signs +,+,-,-,+): maxConsecWins=2, maxConsecLosses=2
    # Positions (buy 6 then unwind 1/1/1/1/2): shares after each bar
    #   6,5,4,3,2,0 -> 5 of 6 bars in market -> exposure = 500/6 %
    return {
        'assumptions': {'initialCash': 100, 'taxBps': 0},
        'equity': [
            {'date': '2024-01-01', 'equity': 100},
            {'date': '2024-02-01', 'equity': 120},
            {'date': '2024-03-01', 'equity': 90},
            {'date': '2024-04-01', 'equity': 110},
            {'date': '2024-05-01', 'equity': 130},
            {'date': '2025-01-01', 'equity': 150},
        ],
        'trades': [
            {'id': 'buy', 'side': 'BUY', 'date': '2024-01-01', 'quantity': 6},
            {'id': 's1', 'side': 'SELL', 'date': '2024-02-01', 'quantity': 1, 'realizedPnl': 20},
            {'id': 's2', 'side': 'SELL', 'date': '2024-03-01', 'quantity': 1, 'realizedPnl': 10},
            {'id': 's3', 'side': 'SELL', 'date': '2024-04-01', 'quantity': 1, 'realizedPnl': -5},
            {'id': 's4', 'side': 'SELL', 'date': '2024-05-01', 'quantity': 1, 'realizedPnl': -15},
            {'id': 's5', 'side': 'SELL', 'date': '2025-01-01', 'quantity': 2, 'realizedPnl': 30},
        ],
        'finalPortfolio': {'realizedPnl': 40, 'unrealizedPnl': 0},
    }


def test_added_metrics_match_hand_calculation():
    result = analyze(synthetic_report())
    m = result['metrics']

    # Sanity: pre-existing metrics unchanged in meaning.
    assert m['maxDrawdownPct'] == pytest.approx(25)
    assert m['winRatePct'] == pytest.approx(60)
    assert m['profitFactor'] == pytest.approx(3)
    assert m['realizedPnl'] == 40

    # Trade-based additive metrics.
    assert m['avgWin'] == pytest.approx(20)
    assert m['avgLoss'] == pytest.approx(10)
    assert m['winLossRatio'] == pytest.approx(2)
    assert m['expectancy'] == pytest.approx(8)
    assert m['maxConsecutiveWins'] == 2
    assert m['maxConsecutiveLosses'] == 2

    # Ratio metrics derived from equity curve.
    days = (date(2025, 1, 1) - date(2024, 1, 1)).days  # 366 (2024 is a leap year)
    cagr = math.expm1(math.log(150 / 100) * 365.25 / days) * 100
    assert m['cagrPct'] == pytest.approx(cagr)
    assert m['calmarRatio'] == pytest.approx(cagr / 25)
    assert m['recoveryFactor'] == pytest.approx(50 / 25)  # net +50% over 25% MDD = 2

    returns = [120 / 100 - 1, 90 / 120 - 1, 110 / 90 - 1, 130 / 110 - 1, 150 / 130 - 1]
    annualized = math.expm1(sum(math.log(1 + r) for r in returns) * 252 / len(returns)) * 100
    assert m['annualizedReturnPct'] == pytest.approx(annualized)
    # Calendar CAGR and per-bar annualized return are genuinely distinct numbers.
    assert m['annualizedReturnPct'] != pytest.approx(m['cagrPct'])

    # Exposure: 5 of 6 bars hold a position.
    assert m['exposurePct'] == pytest.approx(500 / 6)

    # Best / worst month. Monthly returns: 0, +20, -25, +22.22, +18.18, +15.38
    #   best = 2024-04 (90 -> 110 = +200/9 %), worst = 2024-03 (120 -> 90 = -25%)
    assert m['bestMonthReturnPct'] == pytest.approx(200 / 9)
    assert m['worstMonthReturnPct'] == pytest.approx(-25)
    assert result['monthlyExtremes']['best'] == {'month': '2024-04', 'returnPct': pytest.approx(200 / 9)}
    assert result['monthlyExtremes']['worst'] == {'month': '2024-03', 'returnPct': pytest.approx(-25)}

    json.dumps(result, allow_nan=False)


def test_no_losses_leaves_loss_dependent_metrics_none_but_counts_zero():
    r = synthetic_report()
    # Flip the two losing fills positive -> only wins remain.
    r['trades'][3]['realizedPnl'] = 5
    r['trades'][4]['realizedPnl'] = 15
    m = analyze(r)['metrics']
    assert m['avgWin'] == pytest.approx(80 / 5)  # all five fills now positive, gains=80
    assert m['avgLoss'] is None
    assert m['winLossRatio'] is None
    # Zero losing streak is a real, finite count -- not "undefined".
    assert m['maxConsecutiveLosses'] == 0
    assert m['maxConsecutiveWins'] == 5
    assert analyze(r)['metricReasons'].get('avgLoss')


def test_zero_drawdown_leaves_ratios_none():
    r = synthetic_report()
    r['equity'] = [
        {'date': '2024-01-01', 'equity': 100},
        {'date': '2024-06-01', 'equity': 110},
        {'date': '2025-01-01', 'equity': 130},
    ]
    result = analyze(r)
    m = result['metrics']
    assert m['maxDrawdownPct'] == pytest.approx(0)
    # CAGR/MDD and netReturn/MDD are undefined when MDD is zero -> None with a reason.
    assert m['calmarRatio'] is None
    assert m['recoveryFactor'] is None
    assert result['metricReasons'].get('calmarRatio')
    assert result['metricReasons'].get('recoveryFactor')


def test_exposure_zero_with_no_trades_but_none_without_quantities():
    r = synthetic_report()
    r['trades'] = []
    assert analyze(r)['metrics']['exposurePct'] == pytest.approx(0)

    r2 = synthetic_report()
    for t in r2['trades']:
        t.pop('quantity', None)  # no reconstructable position
    res2 = analyze(r2)
    assert res2['metrics']['exposurePct'] is None
    assert res2['metricReasons'].get('exposurePct')


def test_empty_report_degrades_all_added_metrics_to_none():
    result = analyze({'equity': []})
    m = result['metrics']
    for key in ('avgWin', 'avgLoss', 'winLossRatio', 'expectancy',
                'maxConsecutiveWins', 'maxConsecutiveLosses', 'calmarRatio',
                'recoveryFactor', 'annualizedReturnPct', 'exposurePct',
                'bestMonthReturnPct', 'worstMonthReturnPct'):
        assert m[key] is None, key
        assert key in result['metricReasons'], key
    assert result['monthlyExtremes'] == {'best': None, 'worst': None}
    json.dumps(result, allow_nan=False)
