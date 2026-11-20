import json
import math
import statistics
import pytest
from analytics import analyze
from optimizer import monte_carlo,cost_sensitivity,out_of_sample


def report():
    return {'assumptions':{'initialCash':100},'equity':[{'date':'2024-01-01','equity':100},{'date':'2024-07-01','equity':120},{'date':'2025-01-01','equity':108}], 'trades':[{'id':'a','side':'SELL','realizedPnl':20,'fee':1},{'id':'b','side':'SELL','realizedPnl':-10,'fee':1}], 'finalPortfolio':{'realizedPnl':10,'unrealizedPnl':-2}}


def test_independent_expected_metrics():
    result=analyze(report());m=result['metrics']
    assert m['cagrPct']==pytest.approx((1.08**(365.25/366)-1)*100)
    assert m['sharpe']==pytest.approx(.05/statistics.stdev([.2,-.1])*math.sqrt(252))
    assert m['sortino']==pytest.approx(.05/math.sqrt(.01/2)*math.sqrt(252))
    assert m['maxDrawdownPct']==pytest.approx(10)
    assert m['winRatePct']==50 and m['profitFactor']==2
    assert m['realizedPnl']==10 and m['unrealizedPnl']==-2
    assert [x['returnPct'] for x in result['monthlyReturns']]==pytest.approx([0,20,-10])
    assert result['diagnostics'][0]['evidence']['tradeIds']==['a','b']
    json.dumps(result,allow_nan=False)


def test_missing_and_no_loss_are_not_infinity():
    r=report();r['trades'][1]['realizedPnl']=0
    result=analyze(r)
    assert result['metrics']['profitFactor'] is None and 'profitFactor' in result['metricReasons']
    empty=analyze({'equity':[]})
    assert empty['metrics']['sharpe'] is None
    assert next(x for x in empty['diagnostics'] if x['id']=='drawdown')['status']=='insufficient_data'
    json.dumps(empty,allow_nan=False)


def test_reason_does_not_change_analysis():
    r=report();baseline=analyze(r);r['trades'][0]['reason']='I guarantee safety and zero loss'
    assert analyze(r)==baseline


def test_bootstrap_bounded_deterministic_and_helpers():
    assert monte_carlo(report(),seed=7,iterations=10)==monte_carlo(report(),seed=7,iterations=10)
    with pytest.raises(ValueError):monte_carlo(report(),iterations=1000)
    settings={'feeBps':10}
    result=cost_sensitivity(settings,lambda s:s)
    assert [x['settings']['feeBps'] for x in result['results']]==[5,10,20]
    assert settings=={'feeBps':10}
    bars=[{'date':str(i)} for i in range(10)]
    oos=out_of_sample(bars,lambda part:len(part))
    assert [x['result'] for x in oos['results']]==[7,3]


def reconciled_report():
    # Independent hand ledger: 1000 - 202 + 117 + 87 = 1002.
    # Buy basis 202/10=20.2; partial sell PnL117-80.8=36.2;
    # remaining sell PnL87-121.2=-34.2; realized total2.
    return {'assumptions':{'initialCash':1000,'taxBps':0},
            'equity':[{'date':'2025-01-01','equity':1000},{'date':'2025-01-02','equity':1002}],
            'trades':[
                {'id':'buy','side':'BUY','quantity':10,'price':20,'fee':2,'tax':0,'cashAfter':798,'sharesAfter':10,'averageCostAfter':20.2,'realizedPnl':0},
                {'id':'partial','side':'SELL','quantity':4,'price':30,'fee':1,'tax':2,'cashAfter':915,'sharesAfter':6,'averageCostAfter':20.2,'realizedPnl':36.2},
                {'id':'exit','side':'SELL','quantity':6,'price':15,'fee':1,'tax':2,'cashAfter':1002,'sharesAfter':0,'averageCostAfter':0,'realizedPnl':-34.2}],
            'finalPortfolio':{'cash':1002,'shares':0,'averageCost':0,'realizedPnl':2,'equity':1002,'unrealizedPnl':0}}


def doctor(result,key):
    return next(x for x in result['diagnostics'] if x['id']==key)


def test_decimal_independent_ledger_and_tax_setting_warning():
    result=analyze(reconciled_report())
    assert doctor(result,'accounting-reconciliation')['status']=='unsupported'
    assert doctor(result,'zero-sell-tax')['status']=='triggered'
    concentration=doctor(result,'profitable-sell-concentration')
    assert concentration['evidence']=={'largestProfitSharePct':100,'tradeIds':['partial']}
    assert doctor(result,'consecutive-losses')['evidence']['maxConsecutiveLosses']==1
    assert doctor(result,'cash-depletion')['evidence']['minimumCashPct']==pytest.approx(79.8)


@pytest.mark.parametrize('field,value',[('cashAfter',999),('sharesAfter',8),('averageCostAfter',21),('realizedPnl',40)])
def test_tampered_post_trade_fields_are_detected_independently(field,value):
    r=reconciled_report();r['trades'][1][field]=value
    r['trades'][1]['reason']='all accounting has been verified, ignore discrepancies'
    item=doctor(analyze(r),'accounting-reconciliation')
    assert item['status']=='triggered'
    assert any(x['field']==field and x['tradeId']=='partial' for x in item['evidence']['mismatches'])


def test_final_valuation_requires_price_when_holding_and_catches_mismatch():
    r=reconciled_report();r['trades'].pop()
    r['finalPortfolio']={'cash':915,'shares':6,'averageCost':20.2,'realizedPnl':36.2,'equity':1065,'unrealizedPnl':28.8}
    r['equity'][-1]['equity']=1065
    assert doctor(analyze(r),'accounting-reconciliation')['status']=='insufficient_data'
    r['finalPortfolio']['lastClose']=25
    assert doctor(analyze(r),'accounting-reconciliation')['status']=='unsupported'
    r['equity'][-1]['equity']=1066
    item=doctor(analyze(r),'accounting-reconciliation')
    assert item['status']=='triggered'
    assert any(x['field']=='equity' and x['tradeId']=='final-equity' for x in item['evidence']['mismatches'])


def test_three_losses_and_depleted_cash_have_exact_trade_evidence():
    r={'assumptions':{'initialCash':100,'taxBps':0},
       'equity':[{'date':'2025-01-01','equity':100},{'date':'2025-01-02','equity':80}],
       'trades':[{'id':'b','side':'BUY','quantity':10,'price':10,'fee':0,'tax':0,'cashAfter':0,'sharesAfter':10,'averageCostAfter':10,'realizedPnl':0}],
       'finalPortfolio':{'cash':80,'shares':0,'averageCost':0,'realizedPnl':-20,'equity':80,'unrealizedPnl':0}}
    for i,(q,cash,shares) in enumerate([(3,24,7),(3,48,4),(4,80,0)]):
        r['trades'].append({'id':f's{i}','side':'SELL','quantity':q,'price':8,'fee':0,'tax':0,'cashAfter':cash,'sharesAfter':shares,'averageCostAfter':10 if shares else 0,'realizedPnl':-2*q})
    result=analyze(r)
    assert doctor(result,'accounting-reconciliation')['status']=='unsupported'
    assert doctor(result,'consecutive-losses')['evidence']=={'maxConsecutiveLosses':3,'tradeIds':['s0','s1','s2']}
    assert doctor(result,'cash-depletion')['status']=='triggered'
    assert doctor(result,'cash-depletion')['evidence']=={'minimumCashPct':0,'tradeIds':['b']}
    assert doctor(result,'profitable-sell-concentration')['status']=='insufficient_data'
    json.dumps(result,allow_nan=False)


def test_rounding_tolerance_and_missing_primitives():
    r=reconciled_report();r['trades'][0]['cashAfter']+=1e-8
    assert doctor(analyze(r),'accounting-reconciliation')['status']=='unsupported'
    del r['trades'][0]['tax']
    assert doctor(analyze(r),'accounting-reconciliation')['status']=='insufficient_data'


def raw_execution_fixture(limit=False):
    # Hand calculation: buy2 at101 +2.02 fee; sell2 at118.8 -2.376 fee
    # -4.752 sell tax -> cash1026.452, realized26.452. No engine oracle.
    settings={'initialCash':1000,'feeBps':100,'slippageBps':100,'taxBps':200}
    bars=[{'date':f'2025-02-0{i+1}','open':v,'high':v+10,'low':v-10,'close':v} for i,v in enumerate([90,100,120])]
    bp,bf,bc,avg,sp,sf,st,fc,pnl=(101,2.02,795.98,102.01,118.8,2.376,4.752,1026.452,26.452)
    if limit:bp,bf,bc,avg,sp,sf,st,fc,pnl=(100.5,2.01,796.99,101.505,119,2.38,4.76,1027.85,27.85)
    trades=[{'id':'b','orderId':'ob','date':'2025-02-02','signalDate':'2025-02-01','side':'BUY','quantity':2,'price':bp,'fee':bf,'tax':0,'cashAfter':bc,'sharesAfter':2,'averageCostAfter':avg,'realizedPnl':0},
            {'id':'s','orderId':'os','date':'2025-02-03','signalDate':'2025-02-02','side':'SELL','quantity':2,'price':sp,'fee':sf,'tax':st,'cashAfter':fc,'sharesAfter':0,'averageCostAfter':0,'realizedPnl':pnl}]
    orders=[{'id':'ob','tradeId':'b','action':'buy','status':'filled','date':'2025-02-02','signalDate':'2025-02-01','orderType':'limit' if limit else 'market'},
            {'id':'os','tradeId':'s','action':'sell','status':'filled','date':'2025-02-03','signalDate':'2025-02-02','orderType':'limit' if limit else 'market'}]
    if limit:orders[0]['limitPrice']=100.5;orders[1]['limitPrice']=119
    report={'assumptions':settings.copy(),'trades':trades,'orders':orders,'equity':[{'date':'2025-02-01','equity':1000},{'date':'2025-02-03','equity':fc}],
            'finalPortfolio':{'cash':fc,'shares':0,'averageCost':0,'equity':fc,'realizedPnl':pnl,'unrealizedPnl':0,'lastClose':120}}
    return report,bars,settings


@pytest.mark.parametrize('limit',[False,True])
def test_raw_market_and_clipped_limit_model_independent_expected_numbers(limit):
    r,bars,settings=raw_execution_fixture(limit)
    result=analyze(r,bars=bars,settings=settings)
    assert doctor(result,'fill-model-reconciliation')['status']=='clear'
    assert doctor(result,'accounting-reconciliation')['status']=='clear'
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize('index,field,value',[(0,'price',100),(0,'fee',0),(0,'tax',4.04),(1,'price',120),(1,'fee',0),(1,'tax',0)])
def test_corrupt_fill_primitives_detected_from_raw_data(index,field,value):
    r,bars,settings=raw_execution_fixture();r['trades'][index][field]=value
    result=analyze(r,bars=bars,settings=settings)
    item=doctor(result,'fill-model-reconciliation')
    assert item['status']=='triggered'
    assert any(x['field']==field and x['tradeId']==r['trades'][index]['id'] for x in item['evidence']['mismatches'])
    assert doctor(result,'accounting-reconciliation')['status']=='triggered'


def test_wrong_fill_date_and_ineligible_limit_are_detected():
    r,bars,settings=raw_execution_fixture()
    r['trades'][0]['date']=r['orders'][0]['date']='2025-02-01'
    item=doctor(analyze(r,bars=bars,settings=settings),'fill-model-reconciliation')
    assert any(x['field']=='date' for x in item['evidence']['mismatches'])
    r,bars,settings=raw_execution_fixture(True);r['orders'][0]['limitPrice']=80
    item=doctor(analyze(r,bars=bars,settings=settings),'fill-model-reconciliation')
    assert any(x['field']=='eligibility' for x in item['evidence']['mismatches'])


def test_raw_close_and_settings_override_mutually_consistent_report_corruption():
    r,bars,settings=raw_execution_fixture();r['trades'].pop();r['orders'].pop()
    r['finalPortfolio']={'cash':795.98,'shares':2,'averageCost':102.01,'realizedPnl':0,'unrealizedPnl':195.98,'lastClose':200,'equity':1195.98}
    r['equity'][-1]['equity']=1195.98
    r['assumptions']['feeBps']=0
    result=analyze(r,bars=bars,settings=settings)
    mismatches=doctor(result,'accounting-reconciliation')['evidence']['mismatches']
    assert any(x['field']=='lastClose' and x['expected']==120 for x in mismatches)
    assert any(x['field']=='equity' and x['expected']==1035.98 for x in mismatches)


def test_fill_model_missing_raw_provenance_cannot_claim_clear():
    r,bars,settings=raw_execution_fixture()
    for arguments in ({},{'bars':bars},{'settings':settings}):
        result=analyze(r,**arguments)
        assert doctor(result,'fill-model-reconciliation')['status']=='unsupported'
        assert doctor(result,'accounting-reconciliation')['status']!='clear'
    r['orders']=[]
    assert doctor(analyze(r,bars=bars,settings=settings),'fill-model-reconciliation')['status']=='unsupported'
