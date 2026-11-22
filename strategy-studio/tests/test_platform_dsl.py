import copy
import math
import pytest
from runtime.dsl import validate_definition,make_decider,templates,_value


def definition(op='gt'):
    return {'schemaVersion':1,'kind':'dsl','name':'test','entry':{'op':op,'left':{'type':'field','field':'close'},'right':{'type':'constant','value':10}},'exit':{'op':'lt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}}}


def ctx(values,shares=0):
    return {'index':len(values)-1,'history':[{'close':x} for x in values],'portfolio':{'shares':shares,'averageCost':10}}


@pytest.mark.parametrize('bad',[None,[],{}, {'schemaVersion':True}, {'kind':'eval','code':'__import__("os")'}])
def test_bad_definitions(bad):
    with pytest.raises(ValueError):validate_definition(bad)


@pytest.mark.parametrize('value',[float('nan'),float('inf'),True,'__import__("os")',{'eval':'x'}])
def test_bad_constants(value):
    d=definition();d['entry']['right']['value']=value
    with pytest.raises(ValueError):validate_definition(d)


def test_closed_nodes_and_bounds():
    d=definition();d['entry']['eval']='print(1)'
    with pytest.raises(ValueError):validate_definition(d)
    d=definition()
    for _ in range(9): d['entry']={'op':'all','conditions':[d['entry']]}
    with pytest.raises(ValueError):validate_definition(d)


def test_cross_equality_and_no_future():
    decide=make_decider(definition('cross_above'))
    assert decide(ctx([10,11]))['action']=='buy'
    assert decide(ctx([9,10]))['action']=='hold'
    assert decide(ctx([11,12]))['action']=='hold'
    c=ctx([10,11]);c['history'].append({'close':1})
    assert decide(c)['action']=='buy'


def test_warmup_and_indicator_values():
    assert _value({'type':'sma','period':3},[{'close':1}]) is None
    bars=[{'close':x} for x in [1,2,3,4]]
    assert _value({'type':'sma','period':3},bars)==3
    assert _value({'type':'ema','period':3},bars)==3
    assert _value({'type':'rsi','period':2},bars)==100
    assert _value({'type':'rsi','period':2},[{'close':1}]*3)==100
    assert _value({'type':'rsi','period':2},[{'close':x} for x in [3,2,1]])==0
    assert _value({'type':'rsi','period':2},[{'close':x} for x in [1,3,2]])==pytest.approx(200/3)


def test_sell_priority_and_pyramiding_and_risk():
    d=definition();d['exit']=copy.deepcopy(d['entry'])
    assert make_decider(d)(ctx([11],1))['action']=='sell'
    d=definition(); assert make_decider(d)(ctx([11],1))['action']=='hold'
    d['risk']={'stopLossPct':5}; decision=make_decider(d)(ctx([9],1))
    assert decision['action']=='sell' and decision['evidence']['stopLoss'] is True
    assert templates()


@pytest.mark.parametrize('kind', ['sma', 'ema', 'rsi'])
def test_sdk_indicator_parity_at_every_prefix(kind):
    from indicators import calc
    prices=[10,12,11,14,13,13,9,12,17,16]
    operand={'type':kind,'period':3}
    expected=getattr(calc,kind)(prices,3)
    for size in range(1,len(prices)+1):
        actual=_value(operand,[{'close':v} for v in prices[:size]])
        if expected[size-1] is None: assert actual is None
        else: assert actual==pytest.approx(expected[size-1])


def test_independent_seed_and_wilder_smoothing():
    bars=[{'close':v} for v in [10,12,11,15,13]]
    # EMA(3): SMA seed 11, then 13, then 13.
    assert _value({'type':'ema','period':3},bars)==pytest.approx(13)
    # RSI(2): initial gain=1, loss=.5; after +4 => 2.5/.25;
    # after -2 => 1.25/1.125 => 100 - 100/(1 + 10/9).
    assert _value({'type':'rsi','period':2},bars)==pytest.approx(1000/19)
    assert _value({'type':'sma','period':3},bars)==pytest.approx(13)


def test_warmup_does_not_create_cross_and_equality_does():
    d=definition('cross_above')
    d['entry']['left']={'type':'sma','period':2}
    decide=make_decider(d)
    # First defined SMA is above 10, but previous undefined is not a crossing.
    assert decide(ctx([11,12]))['action']=='hold'
    # Previous SMA exactly equals 10; current above 10 is a crossing.
    assert decide(ctx([9,11,12]))['action']=='buy'
    assert decide(ctx([9,11,9]))['action']=='hold'
    d['entry']['op']='cross_below'
    assert make_decider(d)(ctx([11,9,8]))['action']=='buy'


def test_flat_rsi_matches_explicit_sdk_convention():
    # Canonical SDK defines zero average loss as 100, even with zero gain.
    d=definition();d['entry']['left']={'type':'rsi','period':2}
    d['entry']['right']['value']=99
    decide=make_decider(d)
    assert decide(ctx([10,10]))['action']=='hold'
    decision=decide(ctx([10,10,10]))
    assert decision['action']=='buy'
    assert decision['evidence']['rules'][0]['left']==100


def test_precomputed_causal_indicators_equal_prefix_evaluation():
    prices=[10,12,11,14,13,13,9,12,17,16]
    bars=[{'close':x} for x in prices]
    for kind in ('sma','ema','rsi'):
        d=definition('cross_above');d['entry']['left']={'type':kind,'period':3}
        cached=make_decider(d,bars=bars); reference=make_decider(d)
        for index in range(len(bars)):
            context={'index':index,'history':bars[:index+1],'portfolio':{'shares':0,'averageCost':0}}
            assert cached(context)==reference(context)
        mutated=[*bars[:5],*[{'close':1e8} for _ in bars[5:]]]
        altered=make_decider(d,bars=mutated)
        for index in range(5):
            context={'index':index,'history':bars[:index+1],'portfolio':{'shares':0,'averageCost':0}}
            assert cached(context)==altered(context)


def test_published_schema_documents_compound_conditions():
    import jsonschema
    from runtime.dsl import strategy_schema
    d=definition();d['entry']={'op':'all','conditions':[d['entry'],{'op':'gt','left':{'type':'field','field':'close'},'right':{'type':'sma','period':3}}]}
    jsonschema.Draft202012Validator(strategy_schema()).validate(d)
    assert validate_definition(d)['entry']['op']=='all'
    d['entry']['op']='and'
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(d,strategy_schema())
    with pytest.raises(ValueError):validate_definition(d)
