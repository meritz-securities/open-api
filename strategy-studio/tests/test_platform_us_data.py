"""Runtime/MCP integration with invented provider rows; no credentials/network."""
import asyncio
import csv
import io
import datetime as dt
import json
from unittest.mock import Mock

import pytest
from fastmcp import Client, FastMCP
from runtime.mcp import register_platform
from runtime.service import StrategyRuntime, normalize_data
from runtime.storage import digest
from meritz.catalog import load_catalog


def row(day=2, **changes):
    return {'date': f'202501{day:02d}', 'oprc': '10.25', 'hprc': '12.50',
            'lprc': '9.50', 'prpr': '11.75', 'dcpn_cnt': 2, **changes}


@pytest.fixture
def configured(tmp_path):
    registry = Mock()
    registry.resolve.return_value.call.return_value = {'ok': True, 'verified': False,
                                                     'body': {'data': [row(), row(3), row(6)]}}
    return StrategyRuntime(tmp_path, catalog=load_catalog(), registry=registry), registry


def us_args(**changes):
    return {'symbol': 'SOXL', 'startDate': '2023-01-01', 'endDate': '2026-01-01',
            'market': 'US', 'exchange': 'AX', 'quoteMode': '1', **changes}


def test_us_metadata_currency_quality_and_hash_survive_runtime_and_export(configured):
    service, registry = configured
    snapshot = service.dispatch('fetch_data', us_args(account='fixture-account'))
    registry.resolve.assert_called_once_with('fixture-account', require_explicit=True)
    api, params = registry.resolve.return_value.call.call_args.args
    assert api.key == 'ovs_market_candles_days'
    assert params == {'iscd': 'SOXL', 'mrkt_div_code': 'OV', 'mrkt_cls_code': 'AX',
                      'dely_rltm_cls_code': '1', 'mod_stpr_cls_code': '0', 'from': '20230101', 'to': '20260101'}
    registry.resolve.return_value.call.assert_called_once()  # No guessed pagination.
    source = snapshot['source']
    assert {key: source[key] for key in ('market', 'exchange', 'quoteMode', 'currency', 'timezone')} == {
        'market': 'OV', 'exchange': 'AX', 'quoteMode': '1', 'currency': 'USD', 'timezone': 'America/New_York'}
    assert snapshot['startDate'] == '2025-01-02' and snapshot['endDate'] == '2025-01-06'
    assert source['startDate'] == '2023-01-01' and source['endDate'] == '2026-01-01'
    assert any('최대 3년은 앱의 요청 한도' in warning for warning in snapshot['warnings'])
    assert any('응답 코드를 확인할 수 없어' in warning for warning in snapshot['warnings'])
    stored = service.store.get('dataset', snapshot['id'])
    assert stored['bars'][0]['close'] == 11.75  # Not rescaled by dcpn_cnt.
    assert stored['qualityHash'] == digest(stored['warnings'])
    definition=service.templates()['templates'][0]
    definition['entry']={'op':'gt','left':{'type':'field','field':'close'},'right':{'type':'constant','value':0}}
    revision = service.save_strategy(definition)
    run = service.run(revision['id'], snapshot['id'], {'initialCash': 1000})
    assert run['report']['source'] == source
    assert run['manifest']['dataSource'] == source
    assert run['manifest']['dataHash'] == snapshot['contentHash']
    assert run['manifest']['dataWarnings'] == snapshot['warnings']
    assert set(snapshot['warnings']) <= set(run['report']['warnings'])
    assert any('분할 보정 주가를 요청한' in warning for warning in run['report']['warnings'])
    assert all('요청 여부가 기록되지 않아' not in warning for warning in run['report']['warnings'])
    exported = json.loads(service.export_report(run['id'], 'json')['content'])
    assert exported['report']['source']['currency'] == 'USD'
    assert exported['manifest']['dataSource']['timezone'] == 'America/New_York'
    csv_export = service.export_report(run['id'], 'csv')['content'].lstrip('\ufeff')
    trades = list(csv.DictReader(io.StringIO(csv_export)))
    assert trades and all(t['currency'] == 'USD' for t in trades)
    assert all(t['recordType'] == 'trade' and t['dataHash'] == snapshot['contentHash'] for t in trades)
    assert float(trades[0]['price']) == run['report']['trades'][0]['price']
    html_export = service.export_report(run['id'], 'html')['content']
    assert '통화: USD · 시간대: America/New_York · 거래소: AX' in html_export
    no_trade_revision = service.save_strategy(service.templates()['templates'][0])
    no_trade_run = service.run(no_trade_revision['id'], snapshot['id'], {'initialCash': 1000})
    assert no_trade_run['report']['trades'] == []
    empty_export = service.export_report(no_trade_run['id'], 'csv')['content'].lstrip('\ufeff')
    summary = list(csv.DictReader(io.StringIO(empty_export)))
    assert len(summary) == 1 and summary[0]['recordType'] == 'summary'
    assert summary[0]['currency'] == 'USD' and summary[0]['timezone'] == 'America/New_York'
    assert summary[0]['price'] == summary[0]['quantity'] == summary[0]['date'] == ''
    assert service.list_data()['datasets'][0] == snapshot


def test_existing_domestic_default_and_explicit_kr_are_compatible(configured):
    service, registry = configured
    registry.resolve.return_value.call.return_value = {'ok': True, 'body': {'data': [row(stck_clpr='11.75'), row(3, stck_clpr='11.75')]}}
    first = service.fetch_data('005930', '2025-01-01', '2025-01-04')
    second = service.fetch_data('005930', '2025-01-01', '2025-01-04', market='KR')
    assert first['source']['currency'] == second['source']['currency'] == 'KRW'
    assert first['contentHash'] == second['contentHash']
    assert 'exchange' not in first['source'] and 'quoteMode' not in first['source']
    assert all(call.args[0].key == 'market_candles_days' for call in registry.resolve.return_value.call.call_args_list)


def test_fetched_domestic_volume_runs_through_shared_runtime_obv_strategy(configured):
    service, registry = configured
    registry.resolve.return_value.call.return_value = {'ok': True, 'body': {'rsp_cd':'0000','data':[
        {'date':'20250102','oprc':'10','hprc':'12','lprc':'9','stck_clpr':'11','acml_vol':'100'},
        {'date':'20250103','oprc':'11','hprc':'13','lprc':'10','stck_clpr':'12','acml_vol':'200'},
        {'date':'20250104','oprc':'12','hprc':'14','lprc':'11','stck_clpr':'13','acml_vol':'300'},
    ]}}
    dataset = service.fetch_data('005930','2025-01-01','2025-01-05')
    snapshot = service._dataset(dataset['id'])
    assert [bar['volume'] for bar in snapshot['bars']] == [100,200,300]
    definition = {'schemaVersion':1,'kind':'dsl','name':'fetched volume OBV',
        'entry':{'op':'gt','left':{'type':'obv'},'right':{'type':'constant','value':0}},
        'exit':{'op':'lt','left':{'type':'obv'},'right':{'type':'constant','value':0}},
        'quantity':1,'allowPyramiding':False,
        'risk':{'stopLossPct':None,'takeProfitPct':None}}
    revision = service.save_strategy(definition)
    run = service.run(revision['id'], dataset['id'], {'initialCash':1000,'feeBps':0,'slippageBps':0,'taxBps':0})
    assert run['status'] == 'succeeded'
    assert run['report']['strategy']['name'] == 'fetched volume OBV'
    assert run['manifest']['dataHash'] == dataset['contentHash']
    assert run['report']['metrics']['tradeCount'] == 1


@pytest.mark.parametrize('changes', [
    {'market': 'HK'}, {'market': 'OV'}, {'market': None}, {'market': ''},
    {'market': 'KR'}, {'exchange': None}, {'exchange': 'HK'}, {'exchange': ''},
    {'quoteMode': None}, {'quoteMode': 0}, {'quoteMode': 'realtime'}, {'quoteMode': ''},
])
def test_bad_or_ambiguous_market_rejected_before_account_or_provider(configured, changes):
    service, registry = configured
    with pytest.raises(ValueError):
        service.fetch_data(**us_args(**changes))
    registry.resolve.assert_not_called()
    assert service.list_data()['datasets'] == []


@pytest.mark.parametrize('extra', [{'exchange': 'AX'}, {'quoteMode': '1'}])
def test_domestic_cannot_silently_ignore_overseas_fields(configured, extra):
    service, registry = configured
    with pytest.raises(ValueError, match='국내 조회'):
        service.fetch_data('005930', '2025-01-01', '2025-01-04', **extra)
    registry.resolve.assert_not_called()


def test_quote_mode_metadata_participates_in_snapshot_identity(configured):
    service, _ = configured
    delayed = service.fetch_data(**us_args())
    realtime = service.fetch_data(**us_args(quoteMode='0'))
    assert delayed['contentHash'] != realtime['contentHash']
    assert delayed['source']['quoteMode'] == '1' and realtime['source']['quoteMode'] == '0'


def test_40_row_warning_survives_storage_without_pagination(configured):
    service, registry = configured
    start = dt.date(2025, 1, 1)
    registry.resolve.return_value.call.return_value = {'ok': True, 'body': {'rsp_cd': '0000', 'data': [row(date=(start + dt.timedelta(days=i)).strftime('%Y%m%d')) for i in range(40)]}}
    snapshot = service.fetch_data(**us_args())
    assert snapshot['barCount'] == 40
    assert any('응답이 40건' in warning for warning in snapshot['warnings'])
    registry.resolve.return_value.call.assert_called_once()


def test_source_contract_still_rejects_secrets_and_nonstring_metadata(configured):
    service, _ = configured
    snapshot = service.fetch_data(**us_args())
    bars = service.store.get('dataset', snapshot['id'])['bars']
    for update in ({'exchange': ['AX']}, {'quoteMode': 1}, {'app_key': 'forbidden'}):
        with pytest.raises(ValueError):
            normalize_data(bars, {**snapshot['source'], **update})


def test_mcp_us_tool_schema_runtime_and_mode_boundaries(configured):
    service, registry = configured

    async def check():
        mcp = FastMCP('us-data-contract')
        register_platform(mcp, None, None, service)
        async with Client(mcp) as client:
            tool = next(t for t in await client.list_tools() if t.name == 'meritz_platform_data')
            assert all(key in tool.inputSchema['properties'] for key in ('market', 'exchange', 'quoteMode'))
            result = await client.call_tool('meritz_platform_data', {'source': 'meritz', **us_args()})
            snapshot = result.structured_content
            assert snapshot['source']['currency'] == 'USD'
            assert snapshot['source']['timezone'] == 'America/New_York'
            assert snapshot['source']['quoteMode'] == '1'
            count = registry.resolve.return_value.call.call_count
            for bad in [
                {'source': 'demo', 'market': 'US'},
                {'source': 'demo', 'exchange': 'AX'},
                {'source': 'imported', 'bars': [], 'quoteMode': '1'},
                {'source': 'meritz', **us_args(exchange=None)},
                {'source': 'meritz', **us_args(quoteMode=None)},
                {'source': 'meritz', **us_args(market='HK')},
                {'source': 'meritz', **us_args(market='KR')},
            ]:
                failure = await client.call_tool('meritz_platform_data', bad, raise_on_error=False)
                assert failure.is_error
            assert registry.resolve.return_value.call.call_count == count
            demo = await client.call_tool('meritz_platform_data', {'source': 'demo'})
            assert demo.structured_content['source']['kind'] == 'synthetic'
    asyncio.run(check())
