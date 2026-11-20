"""Invented provider fixtures only; never evidence of live SOXL availability."""
import datetime as dt
from unittest.mock import Mock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from meritz import backtest_overseas_data as overseas
from meritz.backtest_data import DataError, normalize
from meritz.catalog import load_catalog
from meritz.client import ApiClient, MeritzError

REQUEST = {'symbol': 'SOXL', 'exchange': 'AX', 'quoteMode': '1',
           'startDate': '2023-01-01', 'endDate': '2026-01-01'}


def row(date='20250102', **values):
    return {'date': date, 'oprc': '40.00', 'hprc': '44.00', 'lprc': '39.00',
            'prpr': '42.35', 'dcpn_cnt': 2, **values}


def setup_provider(rows=None):
    registry = Mock()
    registry.resolve.return_value.call.return_value = {'ok': True, 'body': {'rsp_cd': '0000', 'data': [row()] if rows is None else rows}}
    return load_catalog(), registry


def fetch(catalog, registry, **changes):
    request = {**REQUEST, **changes}
    return overseas.load_overseas_market_data(catalog, registry, request['symbol'], request['startDate'], request['endDate'],
                                              exchange=request['exchange'], quote_mode=request['quoteMode'], account='chosen')


def test_exact_request_and_decimal_prices_not_scaled():
    catalog, registry = setup_provider()
    result = fetch(catalog, registry)
    registry.resolve.assert_called_once_with('chosen', require_explicit=True)
    api, params = registry.resolve.return_value.call.call_args.args
    assert api.key == 'ovs_market_candles_days'
    assert params == {'iscd': 'SOXL', 'mrkt_div_code': 'OV', 'mrkt_cls_code': 'AX',
                      'dely_rltm_cls_code': '1', 'mod_stpr_cls_code': '0', 'from': '20230101', 'to': '20260101'}
    registry.resolve.return_value.call.assert_called_once()
    assert result['candles'] == [{'date': '2025-01-02', 'open': 40., 'high': 44., 'low': 39., 'close': 42.35}]
    for key, value in {'currency': 'USD', 'timezone': 'America/New_York', 'market': 'OV', 'exchange': 'AX',
                       'quoteMode': '1', 'adjustment': 'requested-split-adjusted', 'startDate': '2023-01-01'}.items():
        assert result['source'][key] == value
    assert any('응답 완전성' in w for w in result['warnings'])
    assert any('저장 대상 실제 기간: 2025-01-02 ~ 2025-01-02 · 1봉' in w for w in result['warnings'])
    assert all('TJ' not in w for w in result['warnings'])


def test_us_volume_is_preserved_and_missing_volume_warned():
    catalog, registry = setup_provider([row(acml_vol='500')])
    result = fetch(catalog, registry)
    assert result['candles'][0]['volume'] == 500
    catalog, registry = setup_provider([row()])
    result = fetch(catalog, registry)
    assert any('거래량이 누락' in warning for warning in result['warnings'])


@pytest.mark.parametrize('symbol', ['BRK.B', 'BRK-B', 'soxl'])
@pytest.mark.parametrize('exchange', ['OQ', 'NY', 'AX'])
@pytest.mark.parametrize('mode', ['0', '1'])
def test_symbol_exchange_and_explicit_quote_mode(symbol, exchange, mode):
    result = overseas.parse_request({**REQUEST, 'symbol': symbol, 'exchange': exchange, 'quoteMode': mode})
    assert result['symbol'] == symbol.upper()
    assert result['exchange'] == exchange and result['quoteMode'] == mode


@pytest.mark.parametrize('changes', [{'exchange': 'HK'}, {'quoteMode': 0}, {'quoteMode': None}, {'quoteMode': '2'},
                                    {'symbol': '../SOXL'}, {'symbol': 'A' * 17}, {'symbol': ''}, {'symbol': ' SOXL'},
                                    {'startDate': '2025-02-30'}, {'startDate': '1899-01-01'},
                                    {'endDate': '2026-01-02'}, {'endDate': '2022-12-31'}, {'startDate': '9999-01-01'},
                                    {'app_key': 'must-not-accept'}])
def test_invalid_request_fails_before_account_access(changes):
    catalog, registry = setup_provider()
    with pytest.raises(DataError):
        if 'app_key' in changes:
            overseas.parse_request({**REQUEST, **changes})
        else:
            fetch(catalog, registry, **changes)
    registry.resolve.assert_not_called()


def test_missing_quote_mode_and_leap_year_calendar_limit():
    with pytest.raises(DataError):
        overseas.parse_request({k: v for k, v in REQUEST.items() if k != 'quoteMode'})
    for start, end in [('2024-02-29', '2027-02-28'), ('2023-03-01', '2026-03-01')]:
        assert overseas.parse_request({**REQUEST, 'startDate': start, 'endDate': end})['endDate'] == end
    with pytest.raises(DataError):
        overseas.parse_request({**REQUEST, 'startDate': '2024-02-29', 'endDate': '2027-03-01'})


@pytest.mark.parametrize('utc,expected', [('2025-01-03T04:59:00+00:00', ['2025-01-01']),
                                        ('2025-01-03T05:00:00+00:00', ['2025-01-01', '2025-01-02']),
                                        ('2025-07-03T03:59:00+00:00', ['2025-07-01']),
                                        ('2025-07-03T04:00:00+00:00', ['2025-07-01', '2025-07-02']),
                                        ('2025-03-09T07:00:00+00:00', ['2025-03-07', '2025-03-08']),
                                        ('2025-11-02T06:00:00+00:00', ['2025-11-01'])])
def test_us_current_date_excluded_at_midnight_and_dst(utc, expected):
    now = dt.datetime.fromisoformat(utc)
    dates = sorted(set([date.replace('-', '') for date in expected] + [now.date().strftime('%Y%m%d'),
                   (now.date() - dt.timedelta(days=1)).strftime('%Y%m%d')]))
    candles, _ = normalize([row(date) for date in dates], REQUEST, now,
                           timezone=ZoneInfo('America/New_York'), close_field='prpr', base_warnings=overseas.BASE_WARNINGS)
    assert [c['date'] for c in candles] == expected


def test_missing_timezone_fails_before_provider(monkeypatch):
    catalog, registry = setup_provider()
    monkeypatch.setattr(overseas, 'ZoneInfo', Mock(side_effect=ZoneInfoNotFoundError('private path')))
    with pytest.raises(DataError, match='시간대') as error:
        fetch(catalog, registry)
    assert 'private' not in str(error.value)
    registry.resolve.assert_not_called()


@pytest.mark.parametrize('rows', [[], [row()] * 5001, [row(), row(prpr='41')],
                                 [row(prpr='nan')], [row(prpr=True)], [row(lprc='0.009')],
                                 [row(oprc='1000000001')], [row(hprc='1')], [row(date='20250230')]])
def test_bad_rows_and_unsupported_price_range(rows):
    with pytest.raises(DataError):
        fetch(*setup_provider(rows))


def test_sort_deduplicate_and_bound_warnings():
    result = fetch(*setup_provider([row('20250103'), row(), row(), row('20220101')]))
    assert [c['date'] for c in result['candles']] == ['2025-01-02', '2025-01-03']
    assert any('중복 일봉 1건' in w for w in result['warnings'])
    assert any('기간 밖 일봉 1건' in w for w in result['warnings'])


def test_40_rows_and_unverified_response_do_not_claim_completeness():
    rows = [row((dt.date(2025, 1, 1) + dt.timedelta(days=i)).strftime('%Y%m%d')) for i in range(40)]
    catalog, registry = setup_provider(rows)
    registry.resolve.return_value.call.return_value.update(verified=False, warning='secret provider text')
    result = fetch(catalog, registry)
    assert any('40건' in w and '확정할 수는 없' in w for w in result['warnings'])
    assert any('응답 코드를 확인할 수 없어' in w for w in result['warnings'])
    assert 'secret' not in str(result)


@pytest.mark.parametrize('code', ['ACCOUNT_REQUIRED', 'UNKNOWN_ACCOUNT', 'NO_CREDENTIALS', 'OTHER'])
def test_account_errors_are_fixed_public_messages(code):
    catalog, registry = setup_provider()
    registry.resolve.side_effect = MeritzError('secret account info', code=code)
    with pytest.raises(DataError) as error:
        fetch(catalog, registry)
    assert 'secret' not in str(error.value)


@pytest.mark.parametrize('response', [None, {'ok': False, 'body': {'secret': 'provider'}}, {'ok': True, 'body': []}])
def test_invalid_provider_responses(response):
    catalog, registry = setup_provider()
    registry.resolve.return_value.call.return_value = response
    with pytest.raises(DataError, match='미국 시세 조회에 실패'):
        fetch(catalog, registry)


def test_provider_exception_sanitized_and_missing_catalog_rejected():
    catalog, registry = setup_provider()
    registry.resolve.return_value.call.side_effect = RuntimeError('secret response')
    with pytest.raises(DataError) as error:
        fetch(catalog, registry)
    assert 'secret' not in str(error.value)
    catalog, registry = Mock(), Mock()
    catalog.get.return_value.path = '/wrong'
    with pytest.raises(DataError):
        fetch(catalog, registry)
    registry.resolve.assert_not_called()
    catalog.get.return_value.path = '/market/v1/overseas/candles/days'
    catalog.get.return_value.method = 'GET'
    catalog.get.return_value.param.return_value = None
    with pytest.raises(DataError):
        fetch(catalog, registry)
    registry.resolve.assert_not_called()


def test_domestic_default_close_timezone_and_warnings_unchanged():
    request = {'symbol': '005930', 'market': 'TJ', 'startDate': '2025-01-01', 'endDate': '2025-01-10'}
    candles, warnings = normalize([dict(row(), stck_clpr='41'), dict(row('20250103'), stck_clpr='41')], request,
                                  dt.datetime(2025, 1, 2, 15, tzinfo=dt.timezone.utc))
    assert candles == [{'date': '2025-01-02', 'open': 40., 'high': 44., 'low': 39., 'close': 41.}]
    assert '수정주가를 요청하지만' in warnings[1]
    assert any('1건' in w for w in warnings)


@pytest.mark.parametrize('code', ['5762', '5766', '0001', '5620', '5820', '5822'])
def test_shared_judge_success_does_not_override_endpoint_contract(code):
    catalog, registry = setup_provider()
    api = catalog.get('ovs_market_candles_days')
    judged = ApiClient.judge(api, 200, {'rsp_cd': code, 'data': [row()]}, 'https://fixture.invalid')
    assert judged['ok'] is True
    registry.resolve.return_value.call.return_value = judged
    with pytest.raises(DataError, match='정상 응답 코드 0000'):
        fetch(catalog, registry)
    registry.resolve.return_value.call.assert_called_once()  # No invented pagination.


@pytest.mark.parametrize('code', ['5762', 'SECRET-CODE'])
def test_verified_false_cannot_bypass_readable_unsupported_code(code):
    catalog, registry = setup_provider()
    registry.resolve.return_value.call.return_value.update(verified=False)
    registry.resolve.return_value.call.return_value['body']['rsp_cd'] = code
    with pytest.raises(DataError) as error:
        fetch(catalog, registry)
    assert code not in str(error.value)


@pytest.mark.parametrize('code', [None, '', '\x00'])
def test_real_judge_unreadable_code_warning_policy_retained(code):
    catalog, registry = setup_provider()
    api = catalog.get('ovs_market_candles_days')
    judged = ApiClient.judge(api, 200, {'rsp_cd': code, 'data': [row()]}, 'https://fixture.invalid', degraded_keys={api.key})
    assert judged['ok'] is True and judged['verified'] is False
    registry.resolve.return_value.call.return_value = judged
    result = fetch(catalog, registry)
    assert any('응답 코드를 확인할 수 없어' in w for w in result['warnings'])
    assert result['candles'][0]['close'] == 42.35


def test_unreadable_code_without_explicit_degraded_result_rejected():
    catalog, registry = setup_provider()
    registry.resolve.return_value.call.return_value['body'].pop('rsp_cd')
    with pytest.raises(DataError, match='정상 응답 코드 0000'):
        fetch(catalog, registry)


def test_official_success_with_actual_judge_submessage_gets_fixed_warning():
    catalog, registry = setup_provider()
    api = catalog.get('ovs_market_candles_days')
    body = {'rsp_cd': '0000', 'data': [row()], 'rsp_sub_msg': 'SECRET provider input and account detail'}
    judged = ApiClient.judge(api, 200, body, 'https://fixture.invalid')
    assert judged['ok'] is True and judged.get('note')
    registry.resolve.return_value.call.return_value = judged
    result = fetch(catalog, registry)
    assert any('추가 안내 또는 상세 메시지' in w for w in result['warnings'])
    assert 'SECRET' not in str(result)


@pytest.mark.parametrize('location,key', [('root', 'note'), ('root', 'rsp_sub_msg'), ('body', 'rsp_sub_msg')])
def test_extra_provider_message_presence_is_not_silently_dropped(location, key):
    catalog, registry = setup_provider()
    envelope = registry.resolve.return_value.call.return_value
    (envelope if location == 'root' else envelope['body'])[key] = 'SECRET'
    result = fetch(catalog, registry)
    assert any('추가 안내 또는 상세 메시지' in w for w in result['warnings'])
    assert 'SECRET' not in str(result)


def test_integer_price_string_is_not_rejected_or_scaled_by_decimal_metadata():
    # Specification calls prpr a price string, not a fixed-point integer. Decimal
    # punctuation is not mandated, so G1's proposed punctuation gate is not used.
    result = fetch(*setup_provider([row(oprc='4235', hprc='4236', lprc='4234', prpr='4235', dcpn_cnt=2)]))
    assert result['candles'][0]['close'] == 4235.0


@pytest.mark.parametrize('code,expected', [('RATE_LIMITED', '호출 한도를 초과'), ('AUTH_FAILED', '고객 키의 권한'),
                                           ('UNREACHABLE', '네트워크 연결'), ('BUSINESS_ERROR', '미국 시세 조회에 실패했습니다')])
def test_us_provider_error_codes_map_to_fixed_korean_guidance(code, expected):
    catalog, registry = setup_provider()
    registry.resolve.return_value.call.side_effect = MeritzError('provider secret', code=code)
    with pytest.raises(DataError) as error:
        fetch(catalog, registry)
    assert str(error.value).startswith('미국 ') and expected in str(error.value) and 'secret' not in str(error.value)
