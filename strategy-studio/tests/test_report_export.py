import json
import re

from reports import _svg_chart, export_run


def sample_run():
    return {
        'id': '0123456789abcdef01234567',
        'status': 'succeeded',
        'manifest': {'strategyHash': 'strategy-hash', 'dataHash': 'data-hash', 'engineVersion': '1.0'},
        'analysis': {
            'metrics': {'cagrPct': 8.25, 'sharpe': 0.9, 'sortino': 1.2, 'winRatePct': 50, 'profitFactor': 1.5,
                        'benchmarkReturnPct': 5.5},
            'monthlyReturns': [{'month': '2025-01', 'returnPct': 2.5},
                               {'month': '2025-02', 'returnPct': None,
                                'reason': 'initial equity unavailable or nonpositive'}],
            'assumptions': {
                'tradingDaysPerYear': 252, 'riskFreeRate': 0,
                'cagr': 'calendar 365.25 days; first observed equity to final observed equity',
                'volatility': 'sample standard deviation',
                'downside': 'root mean squared negative returns over all observations',
                'winRateUnit': 'sell fill; breakeven is not win',
                'unrelatedInternalSetting': 'must not be shown',
            },
            'diagnostics': [{'title': '연속 손실', 'status': 'triggered',
                             'message': '<script>위험</script>'},
                            {'title': '<script>상태</script>', 'status': {'invalid': True},
                             'message': '형식이 맞지 않습니다.'}],
        },
        'report': {
            'strategy': {'name': '<script>alert(1)</script>'},
            'source': {'currency': 'KRW', 'timezone': 'Asia/Seoul', 'exchange': 'KRX', 'label': '메리츠 Open API'},
            'metrics': {'totalReturnPct': 8.25, 'maxDrawdownPct': 3.0, 'tradeCount': 2},
            'equity': [
                {'date': '2025-01-01', 'equity': 1000, 'benchmark': 1000, 'drawdownPct': 0},
                {'date': '2025-01-02', 'equity': 1100, 'benchmark': 1050, 'drawdownPct': 0},
                {'date': '2025-01-03', 'equity': 970, 'benchmark': 1030, 'drawdownPct': 11.8},
            ],
            'trades': [{'date': '2025-01-02', 'side': 'BUY', 'quantity': 1, 'price': 1000,
                        'fee': 1, 'tax': 0, 'reason': '<img src=x onerror=alert(1)>'}],
            'warnings': ['실제 수익을 보장하지 않습니다.'],
        },
    }


def test_html_export_contains_printable_charts_metrics_and_baseline_caveat():
    html = export_run(sample_run(), 'html')['content']
    assert html.count('<svg ') == 3
    assert '성과 요약' in html and 'Sharpe' in html and '월별 수익률' in html
    assert '동일 종목 매수 후 보유' in html
    assert 'KOSPI 등 지수 수익률이 아닙니다.' in html
    assert '고점 대비 낙폭' in html
    assert '1,100' in html and '970' in html
    assert '정의·표시 사유' in html
    assert '매도 체결 기준' in html
    assert ('<tr><th scope="row">승률</th><td>50.00%</td><td>전체 매도 체결 중 실현손익이 양수인 비율이며, '
            '본전 거래는 승리로 계산하지 않습니다.</td></tr>') in html
    assert '동일 종목 보유 수익률' in html and '5.50%' in html
    assert '<tr><th scope="row">동일 종목 보유 수익률</th><td>5.50%</td><td></td></tr>' in html
    assert '최대 낙폭 (절대값)' in html
    assert '<td>월초 기준 자산이 없거나 0 이하입니다.</td>' in html
    assert '진단 결과' in html
    assert '<td>확인 필요</td>' in html
    assert '<td>상태 확인 필요</td>' in html
    assert '&lt;script&gt;상태&lt;/script&gt;' in html
    assert '<script>상태</script>' not in html
    assert '&lt;script&gt;위험&lt;/script&gt;' in html
    assert '<script>위험</script>' not in html
    assert '계산 기준' in html
    assert '1년 252거래일' in html
    assert '<th scope="row">무위험 수익률</th><td>0으로 설정</td>' in html
    assert '표본 표준편차 사용' in html
    assert '전체 관측 기간에서 하락 수익률을 제곱 평균한 뒤 제곱근 계산' in html
    assert '본전 거래는 승리로 계산하지 않습니다.' in html
    assert '본전 거래는 승리로 집계하지 않음' in html
    assert 'must not be shown' not in html
    assert '첫 관측 자산부터 마지막 관측 자산까지 달력 일수 기준으로 계산' in html
    run = sample_run()
    run['report']['metrics']['maxDrawdownPct'] = -3
    run['analysis']['assumptions']['volatility'] = 'population standard deviation'
    run['analysis']['assumptions']['winRateUnit'] = 'unknown win-rate basis'
    html = export_run(run, 'html')['content']
    assert '<tr><th scope="row">최대 낙폭 (절대값)</th><td>3.00%</td><td></td></tr>' in html
    assert '계산 기준이 현재 설명과 달라 확인이 필요합니다.' in html
    assert '표본 표준편차 사용' not in html
    assert '승률 집계 기준이 없거나 엔진 정의와 달라 확인이 필요합니다.' in html
    assert '전체 매도 체결 중 실현손익이 양수인 비율' not in html
    assert '세로축은 각각 다르며' in html
    run['analysis']['assumptions']['tradingDaysPerYear'] = 365
    run['analysis']['assumptions']['riskFreeRate'] = False
    html = export_run(run, 'html')['content']
    for invalid_trading_days in (365, 252.0, '252', True):
        run['analysis']['assumptions']['tradingDaysPerYear'] = invalid_trading_days
        html = export_run(run, 'html')['content']
        assert '<th scope="row">연환산 거래일</th><td>계산 기준이 현재 설명과 달라 확인이 필요합니다.</td>' in html
        assert '1년 252거래일' not in html
        assert '1년 365거래일' not in html
    assert '<th scope="row">무위험 수익률</th><td>계산 기준이 현재 설명과 달라 확인이 필요합니다.</td>' in html

    run = sample_run()
    run['analysis']['monthlyReturns'][1]['returnPct'] = 1.0
    html = export_run(run, 'html')['content']
    assert '<th scope="row">2025-02</th><td>1.00%</td><td></td>' in html
    assert '월초 기준 자산이 없거나 0 이하입니다.' not in html

    run = sample_run()
    run['report']['metrics']['maxDrawdownPct'] = None
    html = export_run(run, 'html')['content']
    assert '계산 가능한 자산 평가 자료가 부족하거나 올바르지 않습니다.' in html
    assert '<td>2건</td>' in html
    assert 'default-src &#39;none&#39;' in html
    assert '@media print' in html


def test_html_export_escapes_user_text_and_handles_missing_chart_data():
    run = sample_run()
    run['report']['strategy']['name'] = '<script>alert(1)</script>'
    run['report']['trades'][0]['reason'] = '<img src=x onerror=alert(1)>'
    run['report']['equity'] = [{'date': '2025-01-01', 'equity': 1000}]
    html = export_run(run, 'html')['content']
    assert '<script>alert(1)</script>' not in html
    assert '<img src=x onerror=alert(1)>' not in html
    assert html.count('차트를 만들 만큼 유효한 관측값이 없습니다.') == 3
    assert html.count('<svg ') == 0


def test_html_export_handles_invalid_monthly_values_and_extreme_finite_series():
    run = sample_run()
    run['analysis']['monthlyReturns'] = [
        {'month': '<script>bad</script>', 'returnPct': 'not-a-number', 'reason': 'new engine reason'},
        {'month': '2025-02', 'returnPct': float('nan')},
        {'month': '2025-03', 'returnPct': float('inf')},
        {'month': '2025-04', 'returnPct': True},
    ]
    for row, value in zip(run['report']['equity'], [-1e308, 0, 1e308]):
        row['equity'] = value
    html = export_run(run, 'html')['content']
    assert '&lt;script&gt;bad&lt;/script&gt;' in html
    assert html.count('<td>—</td>') == 4
    assert '월별 수익률 계산 자료가 없거나 올바르지 않습니다.' in html
    assert 'new engine reason' not in html
    assert '월초 기준 자산이 없거나 0 이하입니다.' not in html
    paths = re.findall(r'<path d="([^"]+)"', html)
    assert len(paths) == 3
    assert all(re.fullmatch(r'(?:[ML]-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?(?:\s+|$))+', path) for path in paths)


def test_html_export_explains_unavailable_metric_values():
    run = sample_run()
    run['report']['metrics']['sharpe'] = None
    run['analysis']['metrics']['sharpe'] = None
    run['analysis']['metricReasons'] = {'sharpe': '관측값이 부족합니다.'}
    html = export_run(run, 'html')['content']
    assert '<td>관측값이 부족합니다.</td>' in html

    run['report']['metrics']['sharpe'] = float('nan')
    run['analysis']['metrics']['sharpe'] = 1.1
    html = export_run(run, 'html')['content']
    assert '<td>1.10</td>' in html

    run['report']['metrics']['tradeCount'] = 2.6
    html = export_run(run, 'html')['content']
    assert '0 이상의 정수 체결 건수가 필요합니다.' in html
    run['report']['metrics']['tradeCount'] = -3
    html = export_run(run, 'html')['content']
    assert '0 이상의 정수 체결 건수가 필요합니다.' in html


def test_chart_axes_keep_original_units_and_show_actual_drawdown_bounds():
    rows = sample_run()['report']['equity']
    unknown_currency = _svg_chart(rows, key='equity', label='자산', color='#123456')
    assert '1,100.00' in unknown_currency
    assert '1,100.00%' not in unknown_currency
    assert '통화 미지정' in unknown_currency
    drawdown = _svg_chart(rows, key='drawdownPct', label='낙폭', color='#123456', negative=True)
    assert '0.00%' in drawdown and '-11.80%' in drawdown
    flat_drawdown = _svg_chart(
        [{'date': 'a', 'drawdownPct': 0}, {'date': 'b', 'drawdownPct': 0}],
        key='drawdownPct', label='낙폭', color='#123456', negative=True)
    assert '>0.00%</text>' in flat_drawdown
    assert '>-0.01%</text>' in flat_drawdown
    assert '0.50%' not in flat_drawdown

    flat_equity = _svg_chart(
        [{'date': 'a', 'equity': 1000}, {'date': 'b', 'equity': 1000}],
        key='equity', label='자산', color='#123456', currency='KRW')
    assert '1,020.00' in flat_equity and '980.00' in flat_equity
    assert '500.00' not in flat_equity and '1,500.00' not in flat_equity

    huge = _svg_chart(
        [{'date': 'a', 'equity': 1e308}, {'date': 'b', 'equity': 1.1e308}],
        key='equity', label='자산', color='#123456', currency='KRW')
    assert '1.10000E+308' in huge
    assert len(huge) < 2_000


def test_html_export_falls_back_from_null_metrics_and_handles_older_partial_runs():
    run = sample_run()
    run['report']['metrics']['cagrPct'] = None
    run['analysis']['metrics']['cagrPct'] = 8.25
    html = export_run(run, 'html')['content']
    assert '<td>8.25%</td>' in html

    partial = {'id': 'old-run', 'status': 'succeeded', 'manifest': None,
               'report': {'metrics': None, 'trades': [None], 'warnings': None, 'source': None},
               'analysis': None}
    assert 'Meritz Strategy Intelligence Report' in export_run(partial, 'html')['content']
    assert export_run(partial, 'csv')['filename'] == 'meritz-report-old-run.csv'


def test_exports_reject_path_like_ids_and_csv_trade_keys_cannot_override_metadata():
    for format in ('html', 'csv', 'json'):
        run = sample_run()
        run['id'] = '../../outside'
        try:
            export_run(run, format)
        except ValueError as exc:
            assert 'identifier' in str(exc)
        else:
            raise AssertionError(f'unsafe report id was accepted for {format}')

    run = sample_run()
    run['report']['trades'][0].update(recordType='=formula', currency='=formula', strategyHash='=formula')
    csv = export_run(run, 'csv')['content']
    assert "'=formula" not in csv.splitlines()[1]


def test_html_export_does_not_print_none_for_optional_fields():
    run = sample_run()
    run['report']['strategy']['name'] = None
    run['report']['source']['currency'] = None
    html = export_run(run, 'html')['content']
    assert '<h2>전략</h2>' in html
    assert '통화: 미지정' in html
    assert '>None<' not in html


def test_json_and_csv_export_keep_structured_results_and_escape_spreadsheet_formulas():
    run = sample_run()
    run['report']['trades'][0]['reason'] = '=HYPERLINK("https://example.invalid")'
    exported_json = export_run(run, 'json')
    exported_csv = export_run(run, 'csv')
    assert json.loads(exported_json['content'])['report']['trades'][0]['reason'].startswith('=HYPERLINK')
    assert exported_csv['content'].startswith('\ufeff')
    assert "'=HYPERLINK" in exported_csv['content']
