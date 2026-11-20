"""Independent arithmetic expectations, not generated from broker output."""
import copy
import pytest
from meritz.strategy_engine import run_strategy, DEFAULTS

INFO={'name':'arithmetic','codeHash':'a'*64}

def bars(prices):
    return [{'date':f'2025-01-{i+1:02}','open':p,'high':p,'low':p,'close':p} for i,p in enumerate(prices)]

def run(data, intents, **settings):
    return run_strategy(data,{**DEFAULTS,'initialCash':1000,'feeBps':0,'slippageBps':0,**settings},lambda ctx:copy.deepcopy(intents[ctx['index']]) if ctx['index']<len(intents) else {'action':'hold'},None,INFO)


def test_split_buy_sell_weighted_average_fee_tax_and_realized_arithmetic():
    r=run(bars([50,100,80,120,90]),[{'action':'buy','quantity':2},{'action':'buy','quantity':3},{'action':'sell','quantity':2},{'action':'sell'}],feeBps=100,taxBps=200)
    a,b,c,d=r['trades']
    assert a['cashAfter']==798 and a['averageCostAfter']==101
    assert b['cashAfter']==pytest.approx(555.6)
    assert b['averageCostAfter']==pytest.approx(88.88)
    assert c['tax']==4.8 and c['realizedPnl']==pytest.approx(55.04)
    assert c['cashAfter']==pytest.approx(788.4) and c['sharesAfter']==3
    assert d['realizedPnl']==pytest.approx(-4.74)
    assert r['finalPortfolio']==pytest.approx({'cash':1050.3,'shares':0,'equity':1050.3,'averageCost':0,'realizedPnl':50.3,'unrealizedPnl':0,'lastClose':90})
    assert r['metrics']['totalFees']==pytest.approx(9.5)
    assert r['metrics']['totalTax']==pytest.approx(10.2)
    assert r['metrics']['totalReturnPct']==pytest.approx(5.03)
    assert all(t['tax']==0 for t in r['trades'] if t['side']=='BUY')


def test_open_position_marks_unrealized_using_fee_inclusive_cost():
    r=run(bars([50,100,120]),[{'action':'buy','quantity':2}],feeBps=100)
    assert r['finalPortfolio']['unrealizedPnl']==38
    assert r['finalPortfolio']['realizedPnl']==0
    assert r['finalPortfolio']['equity']==1038


def test_limit_improvement_and_slippage_never_cross_limit():
    data=bars([100,95,105]);data[1].update(low=90,high=105);data[2].update(low=95,high=110)
    r=run(data,[{'action':'buy','quantity':2,'orderType':'limit','limitPrice':100}, {'action':'sell','orderType':'limit','limitPrice':100}],slippageBps=1000)
    assert [t['price'] for t in r['trades']]==[100,100]
    assert [o['status'] for o in r['orders']]==['filled','filled']
    assert r['metrics']['finalEquity']==1000


def test_limit_intrabar_touch_and_day_expiry_no_late_fill():
    data=bars([100,105,90]);data[1].update(low=101,high=110)
    r=run(data,[{'action':'buy','orderType':'limit','limitPrice':100}])
    assert r['trades']==[] and r['orders'][0]['expirationReason']=='DAY_LIMIT_NOT_REACHED'
    data[1]['low']=99
    r=run(data,[{'action':'buy','quantity':1,'orderType':'limit','limitPrice':100}])
    assert r['trades'][0]['price']==100


def test_insufficient_reject_policy_preserves_cash_and_run():
    r=run(bars([100,100,100]),[{'action':'buy','quantity':11},{'action':'sell','quantity':1}],rejectInsufficient=True)
    assert r['trades']==[] and r['finalPortfolio']['cash']==1000
    assert [o['rejectionCode'] for o in r['orders']]==['INSUFFICIENT_CASH','INSUFFICIENT_POSITION']
    with pytest.raises(ValueError,match='초과'):run(bars([100,100]),[{'action':'buy','quantity':11}])


def test_no_next_bar_expires_without_changing_position():
    r=run(bars([100,100]),[{'action':'hold'},{'action':'buy','quantity':1}])
    assert r['orders'][0]['expirationReason']=='NO_NEXT_BAR' and r['trades']==[]


def test_evidence_is_detached_and_reproducible():
    intent={'action':'buy','quantity':1,'evidence':{'indicator':{'period':2,'value':100}}}
    r=run(bars([100,100,110]),[intent]);saved=copy.deepcopy(r)
    intent['evidence']['indicator']['value']=0
    assert r==saved
    assert r==run(bars([100,100,110]),[{'action':'buy','quantity':1,'evidence':{'indicator':{'period':2,'value':100}}}])
    assert r['trades'][0]['evidence']['details']['indicator']['value']==100


@pytest.mark.parametrize('decision',[{'action':'buy','orderType':'limit'},{'action':'buy','orderType':'market','limitPrice':10},{'action':'hold','orderType':'market'},{'action':'buy','evidence':{'value':float('nan')}},{'action':'buy','evidence':{'value':'a'*1001}},{'action':'buy','orderType':[]}])
def test_bad_extended_decisions(decision):
    with pytest.raises(ValueError):run(bars([100,100]),[decision])


def test_source_and_corporate_actions_fail_truthfully():
    data=bars([100,100]);source={'kind':'imported','label':'local CSV','currency':'USD','timezone':'America/New_York'}
    r=run_strategy(data,DEFAULTS,lambda _: {'action':'hold'},source,INFO)
    assert r['source']['kind']=='imported' and any('가져온' in w for w in r['warnings'])
    assert not any('고객 API 키로' in w for w in r['warnings'])
    data[0]['dividend']=1
    with pytest.raises(ValueError,match='기업행동'):run(data,[])


def test_past_prefix_no_future_evidence_and_state_mutation():
    data=bars([100,100,120,110]);seen=[]
    def callback(ctx):
        seen.append(copy.deepcopy(ctx));ctx['history'][0]['close']=999
        return {'action':'buy','quantity':1} if ctx['index']==0 else {'action':'hold'}
    r=run_strategy(data,{**DEFAULTS,'initialCash':1000},callback,None,INFO)
    assert data[0]['close']==100
    assert [len(s['history']) for s in seen]==[1,2,3,4]
    assert seen[2]['portfolio']['averageCost']>0
    assert seen[2]['portfolio']['unrealizedPnl']>0
    assert r['engineVersion']=='shared-broker-2'


def test_user_cannot_spoof_evidence_origin():
    raw={'origin':'host-computed','details':{'profit':999999}}
    r=run(bars([100,100]),[{'action':'buy','quantity':1,'evidence':raw}])
    expected={'origin':'user-asserted','details':raw}
    assert r['evidenceOrigin']=='user-asserted'
    assert r['trades'][0]['evidence']==expected
    assert r['orders'][0]['evidence']==expected
    assert r['decisions'][0]['evidence']==expected
    with pytest.raises(ValueError):
        run_strategy(bars([100,100]),DEFAULTS,lambda _: {'action':'hold'},None,INFO,evidence_origin='trusted-by-user')

def test_trusted_dsl_and_builtin_evidence_origin():
    from meritz.backtest_engine import run_demo
    r=run_strategy(bars([100,100]),DEFAULTS,lambda _: {'action':'buy','quantity':1,'evidence':{'origin':'host-computed'}},None,INFO,evidence_origin='dsl-evaluated')
    assert r['trades'][0]['evidence']['origin']=='dsl-evaluated'
    assert r['trades'][0]['evidence']['details']=={'origin':'host-computed'}
    built_in=run_demo()
    assert built_in['evidenceOrigin']=='host-computed'
    assert all(t['evidence']['origin']=='host-computed' for t in built_in['trades'])


def test_independent_market_slippage_both_sides_and_zero_capacity():
    r=run(bars([50,100,120]),[{'action':'buy','quantity':2},{'action':'sell'}],feeBps=100,taxBps=200,slippageBps=100)
    assert [t['price'] for t in r['trades']]==[101,118.8]
    assert r['trades'][0]['cashAfter']==pytest.approx(795.98)
    assert r['trades'][1]['fee']==pytest.approx(2.376)
    assert r['trades'][1]['tax']==pytest.approx(4.752)
    assert r['metrics']['finalEquity']==pytest.approx(1026.452)
    refused=run(bars([100,2000]),[{'action':'buy'}],rejectInsufficient=True)
    assert refused['orders'][0]['rejectionCode']=='ZERO_CAPACITY'
    assert refused['finalPortfolio']['cash']==1000 and refused['trades']==[]
