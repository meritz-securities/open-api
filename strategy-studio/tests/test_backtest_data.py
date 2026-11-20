import datetime as dt
from unittest.mock import Mock

import pytest

from meritz.backtest_data import DataError, load_market_data, normalize, parse_request
from meritz.catalog import load_catalog
from meritz.client import MeritzError

REQUEST = {"symbol": "005930", "startDate": "2025-01-01", "endDate": "2025-01-10", "market": "TJ"}
NOW = dt.datetime(2025, 1, 10, 12, tzinfo=dt.timezone.utc)


def row(date="20250102", **values):
    return {"date": date, "oprc": 10, "hprc": 12, "lprc": 9, "stck_clpr": 11, **values}


@pytest.mark.parametrize("update", [{"symbol": "../../x"}, {"market": "other"}, {"startDate": "2025-02-30"},
                                    {"startDate": "2023-01-01"}, {"endDate": "2024-12-31"}, {"key": "not-accepted"}])
def test_input(update):
    with pytest.raises(DataError):
        parse_request({**REQUEST, **update})


def test_normalization():
    candles, warnings = normalize([row("20250103"), row(), row(), row("20250110"), row("20250111")], REQUEST, NOW)
    assert [c["date"] for c in candles] == ["2025-01-02", "2025-01-03"]
    assert any("2건" in w for w in warnings)
    assert any("중복" in w for w in warnings)
    candles, _ = normalize([row(), row("20250110")], REQUEST,
                           dt.datetime(2025, 1, 9, 15, tzinfo=dt.timezone.utc))
    assert len(candles) == 1


def test_domestic_volume_is_preserved_for_volume_indicators():
    candles, warnings = normalize([row(acml_vol='1234')], REQUEST, NOW)
    assert candles[0]['volume'] == 1234
    assert not any('거래량이 누락' in warning for warning in warnings)
    candles, warnings = normalize([row()], REQUEST, NOW)
    assert 'volume' not in candles[0]
    assert any('거래량이 누락' in warning for warning in warnings)
    with pytest.raises(DataError, match='거래량'):
        normalize([row(acml_vol='nan')], REQUEST, NOW)


@pytest.mark.parametrize("update", [{"oprc": None}, {"oprc": "nan"}, {"oprc": True}, {"oprc": 0},
                                    {"hprc": 2}, {"lprc": 20}, {"date": "20250230"}])
def test_bad_rows(update):
    with pytest.raises(DataError):
        normalize([row(**update)], REQUEST, NOW)


def test_conflicts_empty_and_bounds():
    for rows in ([row(), row(stck_clpr=10)], [], [row()] * 5001):
        with pytest.raises(DataError):
            normalize(rows, REQUEST, NOW)


def test_reuses_registry_client_and_only_daily_endpoint():
    catalog = load_catalog()
    registry = Mock()
    client = registry.resolve.return_value
    client.call.return_value = {"ok": True, "body": {"data": [row()]}}
    result = load_market_data(catalog, registry, "005930", "2025-01-01", "2025-01-10", account="selected")
    registry.resolve.assert_called_once_with("selected", require_explicit=True)
    api, params = client.call.call_args.args
    assert api.key == "market_candles_days"
    assert params == {"iscd": "005930", "mrkt_div_code": "TJ", "from": "20250101", "to": "20250110",
                      "exch_use_yn": "Y", "mod_stpr_cls_code": "0"}
    assert result["source"]["kind"] == "meritz"
    assert result["source"]["symbol"] == "005930"
    assert result["source"]["adjustment"] == "requested-adjusted"
    assert any("별도로 검증하지" in w for w in result["warnings"])


def test_degraded_response_warning_is_preserved_without_provider_text():
    catalog, registry = load_catalog(), Mock()
    registry.resolve.return_value.call.return_value = {
        "ok": True, "body": {"data": [row()]}, "verified": False, "warning": "sensitive provider text"}
    result = load_market_data(catalog, registry, "005930", "2025-01-01", "2025-01-10")
    assert any("응답 코드를 확인할 수 없어" in w for w in result["warnings"])
    assert all("sensitive" not in w for w in result["warnings"])


def test_domestic_truncation_and_provider_notes_are_disclosed_without_trusting_rsp_code():
    catalog, registry = load_catalog(), Mock()
    rows = [row((dt.date(2025, 1, 1) + dt.timedelta(days=i)).strftime('%Y%m%d')) for i in range(40)]
    registry.resolve.return_value.call.return_value = {'ok': True, 'body': {'rsp_cd':'0000','data':rows}}
    result = load_market_data(catalog, registry, '005930', '2025-01-01', '2025-12-31')
    assert any('40건' in warning and '잘림 가능성' in warning for warning in result['warnings'])
    registry.resolve.return_value.call.return_value = {
        'ok': True, 'body': {'rsp_cd':'5762','rsp_sub_msg':'private provider text','data':[row()]},
        'note':'다음 페이지가 있습니다. private provider text'}
    result = load_market_data(catalog, registry, '005930', '2025-01-01', '2025-12-31')
    assert any('다음 페이지 가능성' in warning for warning in result['warnings'])
    assert any('추가 안내' in warning for warning in result['warnings'])
    assert 'private provider text' not in str(result)


def test_account_and_provider_errors_sanitized():
    catalog, registry = load_catalog(), Mock()
    registry.resolve.side_effect = MeritzError("secret account label", code="ACCOUNT_REQUIRED")
    with pytest.raises(DataError) as e:
        load_market_data(catalog, registry, "005930", "2025-01-01", "2025-01-10")
    assert "secret" not in str(e.value)
    registry.resolve.side_effect = None
    registry.resolve.return_value.call.side_effect = RuntimeError("secret response body")
    with pytest.raises(DataError) as e:
        load_market_data(catalog, registry, "005930", "2025-01-01", "2025-01-10")
    assert "secret" not in str(e.value)


def test_missing_range_catalog_fails_before_registry():
    catalog, registry = Mock(), Mock()
    api = catalog.get.return_value
    api.path, api.method = "/market/v1/candles/days", "GET"
    api.param.return_value = None
    with pytest.raises(DataError):
        load_market_data(catalog, registry, "005930", "2025-01-01", "2025-01-10")
    registry.resolve.assert_not_called()
def test_import_without_os_timezone_database():
    import os
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c', 'import meritz.server; from meritz.backtest_data import SEOUL; import datetime; assert SEOUL.utcoffset(None) == datetime.timedelta(hours=9)'],
                            env={**os.environ, 'PYTHONTZPATH': '', 'MERITZ_NO_ENV_FILE': '1'},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("code,status,expected", [
    ("RATE_LIMITED", 500, "호출 한도를 초과"),
    ("AUTH_FAILED", 403, "고객 키의 권한"),
    ("AUTH_BAD_RESPONSE", 200, "고객 키의 권한"),
    ("NO_CREDENTIALS", None, "고객 키의 권한"),
    ("TIMEOUT", None, "네트워크 연결"),
    ("UNREACHABLE", None, "네트워크 연결"),
    ("AUTH_UNREACHABLE", None, "네트워크 연결"),
    ("BUSINESS_ERROR", 500, "시세 조회에 실패했습니다"),
    (None, None, "시세 조회에 실패했습니다"),
])
def test_provider_error_codes_map_to_fixed_korean_guidance(code, status, expected):
    """R-10: client 오류 분류 코드별 고정 안내. 공급자 원문(rsp_msg·body)은 노출하지 않는다."""
    catalog, registry = load_catalog(), Mock()
    client = registry.resolve.return_value
    client.call.side_effect = MeritzError("provider secret text EGW00200", status=status, body={"rsp_msg": "provider secret body"}, code=code)
    with pytest.raises(DataError) as error:
        load_market_data(catalog, registry, "005930", "2025-01-01", "2025-01-10")
    assert expected in str(error.value) and "secret" not in str(error.value) and "EGW" not in str(error.value)
    # judge() 가 예외 대신 ok=False 결과(RATE_LIMITED 등)로 돌려주는 경로도 같은 문구여야 한다.
    client.call.side_effect = None
    client.call.return_value = {"ok": False, "error": code, "http_status": status, "message": "provider secret", "body": {"rsp_msg": "secret"}}
    with pytest.raises(DataError) as error:
        load_market_data(catalog, registry, "005930", "2025-01-01", "2025-01-10")
    assert expected in str(error.value) and "secret" not in str(error.value)
