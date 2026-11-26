"""Credential-free report exports. All user-provided text is escaped."""
import csv
import html
import io
import json
import math
import re
from decimal import Decimal


def _svg_chart(rows, *, key, label, color, currency=None, negative=False):
    """Build a script-free, self-contained SVG from trusted numeric report fields."""
    points = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get('date') is None or isinstance(row.get(key), bool):
            continue
        try:
            value = float(row[key])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(value):
            points.append((str(row.get('date', '')), -abs(value) if negative else value))
    if len(points) < 2:
        return '<p class="empty">차트를 만들 만큼 유효한 관측값이 없습니다.</p>'

    width, height = 960, 240
    left, right, top, bottom = 52, width - 20, 18, height - 34
    raw_values = [value for _, value in points]
    scale = max(max(abs(value) for value in raw_values), 1.0)
    values = [value / scale for value in raw_values]
    low = min(values)
    high = max(values)
    if negative:
        high = max(0.0, high)
    span = high - low
    if span == 0:
        if negative:
            low -= 0.01
        else:
            span = max(abs(high) * 0.04, 0.02)
            low -= span / 2
            high += span / 2
    x = lambda index: left + index * (right - left) / (len(points) - 1)
    y = lambda value: bottom - (value - low) * (bottom - top) / (high - low)
    path = ' '.join(('M' if i == 0 else 'L') + f'{x(i):.2f},{y(value):.2f}'
                    for i, value in enumerate(values))
    zero = ''
    if negative and low <= 0 <= high:
        zero = f'<line x1="{left}" x2="{right}" y1="{y(0):.2f}" y2="{y(0):.2f}" class="zero" />'
    unit = (f' · {html.escape(str(currency))}' if currency else
            (' · %' if negative else ' · 통화 미지정'))
    def axis_value(value):
        if abs(value) >= Decimal('1e12'):
            return f'{value:.5E}'
        if currency:
            return f'{value:,.2f}'
        if negative:
            return f'{value:.2f}%'
        return f'{value:,.2f}'
    high_value = Decimal(str(high)) * Decimal(str(scale))
    low_value = Decimal(str(low)) * Decimal(str(scale))
    start = html.escape(points[0][0])
    end = html.escape(points[-1][0])
    return (
        f'<svg class="report-chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{html.escape(label, quote=True)}">'
        f'<line x1="{left}" x2="{right}" y1="{top}" y2="{top}" class="grid" />'
        f'<line x1="{left}" x2="{right}" y1="{bottom}" y2="{bottom}" class="grid" />'
        f'{zero}<path d="{path}" fill="none" stroke="{color}" stroke-width="3" '
        'stroke-linecap="round" stroke-linejoin="round" />'
        f'<text x="{left}" y="{top + 12}">{html.escape(axis_value(high_value))}</text>'
        f'<text x="{left}" y="{bottom - 4}">{html.escape(axis_value(low_value))}</text>'
        f'<text x="{left}" y="{height - 8}">{start}</text>'
        f'<text x="{right}" y="{height - 8}" text-anchor="end">{end}</text>'
        f'<text x="{right}" y="14" text-anchor="end">{html.escape(label)}{unit}</text>'
        '</svg>'
    )


def _metric_rows(run):
    report = run.get('report') if isinstance(run.get('report'), dict) else {}
    metrics = report.get('metrics') if isinstance(report.get('metrics'), dict) else {}
    analysis = run.get('analysis') if isinstance(run.get('analysis'), dict) else {}
    extra = analysis.get('metrics') if isinstance(analysis.get('metrics'), dict) else {}
    reasons = analysis.get('metricReasons') if isinstance(analysis.get('metricReasons'), dict) else {}
    assumptions = analysis.get('assumptions') if isinstance(analysis.get('assumptions'), dict) else {}
    labels = (
        ('totalReturnPct', '총 수익률', '%'), ('benchmarkReturnPct', '동일 종목 보유 수익률', '%'),
        ('cagrPct', 'CAGR', '%'), ('maxDrawdownPct', '최대 낙폭 (절대값)', '%'), ('sharpe', 'Sharpe', ''),
        ('sortino', 'Sortino', ''), ('winRatePct', '승률', '%'),
        ('profitFactor', 'Profit Factor', ''), ('tradeCount', '체결 수', '건'),
    )
    rows = []
    def usable(key, value):
        if value is None or isinstance(value, bool):
            return False
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return False
        return math.isfinite(number) and (key != 'tradeCount' or (number >= 0 and number.is_integer()))

    for key, label, unit in labels:
        value = metrics.get(key)
        if not usable(key, value):
            value = extra.get(key)
        if not usable(key, value):
            display = '—'
        else:
            try:
                number = float(value)
                if not math.isfinite(number):
                    display = '—'
                elif key == 'tradeCount':
                    display = f'{number:,.0f}{unit}'
                else:
                    if key == 'maxDrawdownPct':
                        number = abs(number)
                    display = f'{number:,.2f}{unit}'
            except (TypeError, ValueError, OverflowError):
                display = '—'
        meaning = ''
        if key == 'winRatePct':
            if assumptions.get('winRateUnit') == 'sell fill; breakeven is not win':
                meaning = '전체 매도 체결 중 실현손익이 양수인 비율이며, 본전 거래는 승리로 계산하지 않습니다.'
            else:
                meaning = '승률 집계 기준이 없거나 엔진 정의와 달라 확인이 필요합니다.'
        if display == '—':
            reason = reasons.get(key)
            if isinstance(reason, str) and any('\uac00' <= char <= '\ud7a3' for char in reason):
                explanation = reason
            elif key == 'tradeCount':
                explanation = '0 이상의 정수 체결 건수가 필요합니다.'
            elif key == 'cagrPct':
                explanation = '처음과 마지막 자산이 양수이고 관측 기간이 있어야 계산할 수 있습니다.'
            elif key == 'sharpe':
                explanation = '변동성이 있는 수익률 관측값이 2개 이상 필요합니다.'
            elif key == 'sortino':
                explanation = '하락 수익률을 포함한 관측값이 2개 이상 있어야 계산할 수 있습니다.'
            elif key == 'maxDrawdownPct':
                explanation = '계산 가능한 자산 평가 자료가 부족하거나 올바르지 않습니다.'
            elif key == 'winRatePct':
                explanation = '모든 매도 체결의 실현손익 자료가 있어야 계산할 수 있습니다.'
            elif key == 'profitFactor':
                explanation = '실현 손실 또는 전체 매도 체결의 손익 자료가 있어야 계산할 수 있습니다.'
            else:
                explanation = '계산에 필요한 자료가 부족하거나 올바르지 않습니다.'
        else:
            explanation = meaning
        rows.append('<tr><th scope="row">' + html.escape(label) + '</th><td>' +
                    html.escape(display) + '</td><td>' + html.escape(str(explanation or '')) + '</td></tr>')
    return ''.join(rows)


def _assumption_rows(analysis):
    assumptions = analysis.get('assumptions') if isinstance(analysis, dict) else None
    if not isinstance(assumptions, dict):
        return ''
    labels = {
        'tradingDaysPerYear': ('연환산 거래일', lambda value: '1년 252거래일' if type(value) is int and value == 252 else None),
        'riskFreeRate': ('무위험 수익률', lambda value: '0으로 설정' if not isinstance(value, bool) and isinstance(value, (int, float)) and value == 0 else None),
        'cagr': ('연복리 수익률', lambda value: '첫 관측 자산부터 마지막 관측 자산까지 달력 일수 기준으로 계산' if value == 'calendar 365.25 days; first observed equity to final observed equity' else None),
        'volatility': ('변동성', lambda value: '표본 표준편차 사용' if value == 'sample standard deviation' else None),
        'downside': ('하락 위험', lambda value: '전체 관측 기간에서 하락 수익률을 제곱 평균한 뒤 제곱근 계산' if value == 'root mean squared negative returns over all observations' else None),
        'winRateUnit': ('승률 집계 기준', lambda value: '매도 체결 기준이며, 본전 거래는 승리로 집계하지 않음' if value == 'sell fill; breakeven is not win' else None),
    }
    rows = []
    for key, (label, explain) in labels.items():
        if key not in assumptions or assumptions[key] is None:
            continue
        description = explain(assumptions[key]) or '계산 기준이 현재 설명과 달라 확인이 필요합니다.'
        rows.append('<tr><th scope="row">' + html.escape(label) + '</th><td>' +
                    html.escape(description) + '</td></tr>')
    return ''.join(rows)


def _diagnostic_rows(analysis):
    diagnostics = analysis.get('diagnostics') if isinstance(analysis, dict) else None
    if not isinstance(diagnostics, list):
        return ''
    statuses = {'triggered': '확인 필요', 'clear': '설정 기준에 해당하지 않음',
                'insufficient_data': '자료 부족', 'unsupported': '분석 범위에 없음'}
    rows = []
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        title = item.get('title') if isinstance(item.get('title'), str) else '진단 항목'
        message = item.get('message') if isinstance(item.get('message'), str) else ''
        raw_status = item.get('status')
        status = statuses.get(raw_status, '상태 확인 필요') if isinstance(raw_status, str) else '상태 확인 필요'
        rows.append('<tr><th scope="row">' + html.escape(title) + '</th><td>' +
                    html.escape(status) + '</td><td>' + html.escape(message) + '</td></tr>')
    return ''.join(rows)


def export_run(run, format):
    if format not in ('json', 'csv', 'html'):
        raise ValueError('Supported exports: json, csv, html. PDF uses the browser print dialog.')
    if run.get('status') != 'succeeded':
        raise ValueError('Only completed reports can be exported')
    run_id = str(run.get('id', ''))
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
        raise ValueError('Report identifier is invalid')
    filename = 'meritz-report-' + run_id + '.' + format
    if format == 'json':
        try:
            content = json.dumps(run, ensure_ascii=False, indent=2, allow_nan=False)
        except ValueError as exc:
            raise ValueError('Report contains a non-finite number and cannot be exported as JSON') from exc
        return {'filename': filename, 'mimeType': 'application/json', 'content': content}
    if format == 'csv':
        stream = io.StringIO(newline=''); writer = csv.writer(stream)
        report = run.get('report') if isinstance(run.get('report'), dict) else {}
        source = report.get('source') if isinstance(report.get('source'), dict) else {}
        manifest = run.get('manifest') if isinstance(run.get('manifest'), dict) else {}
        metadata = {'currency': source.get('currency') or 'unspecified',
                    'timezone': source.get('timezone') or '', 'exchange': source.get('exchange') or '',
                    'dataSource': source.get('label') or '', 'strategyHash': manifest.get('strategyHash') or '',
                    'dataHash': manifest.get('dataHash') or '', 'engineVersion': manifest.get('engineVersion') or ''}
        columns = ['recordType', *metadata, 'date', 'signalDate', 'side', 'quantity', 'price', 'fee', 'tax', 'cashAfter', 'sharesAfter', 'realizedPnl', 'reason']
        trade_columns = {'date', 'signalDate', 'side', 'quantity', 'price', 'fee', 'tax',
                         'cashAfter', 'sharesAfter', 'realizedPnl', 'reason'}
        writer.writerow(columns)
        trades = report.get('trades') if isinstance(report.get('trades'), list) else []
        # A labelled summary row retains provenance for zero-trade runs without
        # inventing a transaction. Existing trade field names stay unchanged.
        rows = ([{'recordType': 'trade', **metadata,
                  **{key: row.get(key, '') for key in trade_columns if key in row}}
                 for row in trades if isinstance(row, dict)] or
                [{'recordType': 'summary', **metadata}])
        for row in rows:
            cells = []
            for key in columns:
                value = row.get(key, '')
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                    value = "'" + value
                cells.append(value)
            writer.writerow(cells)
        return {'filename': filename, 'mimeType': 'text/csv;charset=utf-8', 'content': '\ufeff' + stream.getvalue()}
    e = lambda value: html.escape('' if value is None else str(value), quote=True)
    report = run.get('report') if isinstance(run.get('report'), dict) else {}
    source = report.get('source') if isinstance(report.get('source'), dict) else {}
    source_line = ' · '.join(e(label) + ': ' + e(source.get(key) or '미지정') for key, label in [('currency', '통화'), ('timezone', '시간대'), ('exchange', '거래소')])
    trades = report.get('trades') if isinstance(report.get('trades'), list) else []
    rows = ''.join('<tr>' + ''.join('<td>' + e(t.get(k, '')) + '</td>' for k in ('date', 'side', 'quantity', 'price', 'fee', 'tax', 'reason')) + '</tr>' for t in trades if isinstance(t, dict))
    warning_items = report.get('warnings') if isinstance(report.get('warnings'), list) else []
    warnings = ''.join('<li>' + e(w) + '</li>' for w in warning_items)
    manifest = run.get('manifest') if isinstance(run.get('manifest'), dict) else {}
    facts = ''.join('<dt>' + e(k) + '</dt><dd>' + e(v) + '</dd>' for k, v in manifest.items())
    strategy = report.get('strategy') if isinstance(report.get('strategy'), dict) else {}
    if not strategy.get('name'):
        strategy = {'name': '전략'}
    equity = report.get('equity') if isinstance(report.get('equity'), list) else []
    currency = source.get('currency')
    equity_chart = _svg_chart(equity, key='equity', label='전략 자산', color='#c93743', currency=currency)
    baseline_chart = _svg_chart(equity, key='benchmark', label='동일 종목 매수 후 보유', color='#475569', currency=currency)
    drawdown_chart = _svg_chart(equity, key='drawdownPct', label='고점 대비 낙폭', color='#2563eb', negative=True)
    summary_metrics = _metric_rows(run)
    analysis = run.get('analysis') if isinstance(run.get('analysis'), dict) else {}
    assumptions = _assumption_rows(analysis)
    diagnostics = _diagnostic_rows(analysis)
    monthly = analysis.get('monthlyReturns') if isinstance(analysis.get('monthlyReturns'), list) else []
    monthly_rows_list = []
    for item in monthly:
        if not isinstance(item, dict):
            continue
        value = item.get('returnPct')
        try:
            if isinstance(value, bool):
                raise ValueError('boolean is not a monthly return')
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            value = None
        display = f'{value:.2f}%' if value is not None and math.isfinite(value) else '—'
        reason = item.get('reason')
        reason_display = ''
        if display == '—':
            if reason == 'initial equity unavailable or nonpositive':
                reason_display = '월초 기준 자산이 없거나 0 이하입니다.'
            else:
                reason_display = '월별 수익률 계산 자료가 없거나 올바르지 않습니다.'
        monthly_rows_list.append('<tr><th scope="row">' + e(item.get('month', '')) + '</th><td>' +
                                 e(display) + '</td><td>' + e(reason_display) + '</td></tr>')
    monthly_rows = ''.join(monthly_rows_list)
    content = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; img-src data:; base-uri &#39;none&#39;; form-action &#39;none&#39;"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Meritz Strategy Report</title><style>body{font:15px/1.6 system-ui;max-width:1100px;margin:auto;padding:30px;color:#25303d}h1{color:#be2941}table{width:100%;border-collapse:collapse}td,th{padding:9px;border-bottom:1px solid #ddd;overflow-wrap:anywhere}dt{font-weight:bold}dd{margin:0 0 10px;overflow-wrap:anywhere}.scroll{overflow:auto}.charts{display:grid;grid-template-columns:1fr;gap:16px}.chart{border:1px solid #d8dee8;border-radius:10px;padding:12px}.chart h3{margin:0}.report-chart{display:block;width:100%;height:auto;overflow:visible}.report-chart text{font:12px system-ui;fill:#596579}.grid,.zero{stroke:#dfe4ec;stroke-width:1}.zero{stroke-dasharray:4 4}.metrics{max-width:760px}.metrics td:last-child{color:#667085;font-size:.9em}.empty{color:#667085}@media print{body{padding:0;font-size:11px}.chart{break-inside:avoid}}@media(min-width:760px){.charts{grid-template-columns:1fr 1fr}.chart:first-child{grid-column:1/-1}}</style><h1>Meritz Strategy Intelligence Report</h1><p>과거 데이터의 가상 체결 결과 · 실제 수익 보장 없음</p><h2>' + e(strategy.get('name', '전략')) + '</h2><p>' + source_line + '</p><h2>성과 요약</h2><div class="scroll"><table class="metrics"><thead><tr><th>항목</th><th>값</th><th>정의·표시 사유</th></tr></thead><tbody>' + summary_metrics + '</tbody></table></div><h2>자산과 위험 변화</h2><p>비교선은 같은 종목을 기간 시작 시점에 매수해 보유한 기준선입니다. KOSPI 등 지수 수익률이 아닙니다. 전략과 비교선 차트의 세로축은 각각 다르며, 수익률 차이는 성과 요약을 기준으로 비교하세요.</p><div class="charts"><section class="chart"><h3>전략 자산</h3>' + equity_chart + '</section><section class="chart"><h3>비교 기준</h3>' + baseline_chart + '</section><section class="chart"><h3>고점 대비 낙폭</h3>' + drawdown_chart + '</section></div><h2>월별 수익률</h2><div class="scroll"><table><thead><tr><th>월</th><th>수익률</th><th>표시 사유</th></tr></thead><tbody>' + (monthly_rows or '<tr><td colspan="3">표시할 월별 자료가 없습니다.</td></tr>') + '</tbody></table></div><h2>진단 결과</h2><div class="scroll"><table><thead><tr><th>항목</th><th>상태</th><th>설명</th></tr></thead><tbody>' + (diagnostics or '<tr><td colspan="3">진단 자료가 없습니다.</td></tr>') + '</tbody></table></div><h2>계산 기준</h2><p>성과 요약에 표시한 지표의 계산 방식입니다. 엔진이 사용하는 기준과 다르면 확인이 필요하다고 표시합니다.</p><div class="scroll"><table><thead><tr><th>항목</th><th>적용 기준</th></tr></thead><tbody>' + (assumptions or '<tr><td colspan="2">계산 기준 자료가 없습니다.</td></tr>') + '</tbody></table></div><h2>실행 증거</h2><dl>' + facts + '</dl><h2>거래 내역</h2><div class="scroll"><table><tr><th>일자</th><th>구분</th><th>수량</th><th>가격</th><th>수수료</th><th>세금</th><th>근거</th></tr>' + rows + '</table></div><h2>가정과 한계</h2><ul>' + warnings + '</ul></html>'
    return {'filename': filename, 'mimeType': 'text/html;charset=utf-8', 'content': content}
