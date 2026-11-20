import importlib.util
from pathlib import Path
import sys
import pytest
from meritz.strategy_runner import StrategySession, validate_code

spec=importlib.util.spec_from_file_location('meritz_strategy',Path(__file__).parents[1]/'sdk/meritz_strategy/__init__.py')
sdk=importlib.util.module_from_spec(spec);spec.loader.exec_module(sdk)

def observation():
    return {'index':2,'date':'2025-01-03','bar':{'close':3},'history':[{'close':v} for v in (1,2,3)],'portfolio':{'cash':100,'shares':2,'equity':106,'averageCost':2,'realizedPnl':1,'unrealizedPnl':2}}

def test_context_helpers_have_no_execution_side_effects():
    ctx=sdk.Context(observation())
    assert ctx.get_cash()==100
    assert ctx.get_position()['quantity']==2
    assert ctx.buy(2,order_type='limit',limit_price=5)['limitPrice']==5
    assert ctx.close_position()['action']=='sell'
    p=ctx.get_portfolio();p['cash']=0
    assert ctx.get_cash()==100
    assert ctx.get_indicator('sma',2)==2.5
    assert ctx.get_indicator('ema',2)==2.5
    assert ctx.get_indicator('rsi',2)==100
    assert ctx.get_indicator('sma',4) is None
    with pytest.raises(ValueError):ctx.get_indicator('unknown',2)

@pytest.mark.skipif(sys.platform!='darwin',reason='Python strategy sandbox is macOS only')
def test_sdk_import_and_initialize_once_inside_unchanged_sandbox():
    code='''from meritz_strategy import Context
state = 0
def initialize(ctx):
    global state
    state += 1
def on_bar(raw):
    ctx = Context(raw)
    assert state == 1
    return ctx.buy(1, evidence={"sma":ctx.get_indicator("sma",2)})
'''
    assert validate_code(code)['valid']
    with StrategySession(code) as session:
        for _ in range(2):
            result=session.decide(observation())
            assert result['quantity']==1 and result['evidence']['sma']==2.5


def test_optional_initializer_signature_validation():
    assert not validate_code('def initialize(): pass\ndef on_bar(ctx): pass')['valid']
    assert not validate_code('async def initialize(ctx): pass\ndef on_bar(ctx): pass')['valid']
    assert validate_code('def initialize(ctx): pass\ndef on_bar(ctx): pass')['valid']

def test_sdk_uses_shared_indicator_semantics_for_flat_rsi():
    raw=observation();raw['history']=[{'close':1}]*3
    assert sdk.Context(raw).get_indicator('rsi',2)==100
