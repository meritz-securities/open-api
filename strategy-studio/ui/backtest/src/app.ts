import { App } from '@modelcontextprotocol/ext-apps';
import type { Report } from './contracts';
import './style.css';

type Trade = Report['trades'][number];
type Result = { structuredContent?: unknown; isError?: boolean; content?: unknown[] };
type Comparison = { baseline: Report; candidate: Report };
const preview = new URLSearchParams(location.search).get('preview') === '1';
const local = !preview && new URLSearchParams(location.search).get('local') === '1';
const localToken = document.querySelector<HTMLMetaElement>('meta[name="backtest-local-token"]')?.content;
const app = new App({ name: 'Meritz Strategy Report', version: '0.1.0' });
const el = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
const runButton = el<HTMLButtonElement>('run-button');
const compareButton = el<HTMLButtonElement>('compare-button');
const codeEditor = el<HTMLTextAreaElement>('strategy-code');
const validateButton = el<HTMLButtonElement>('validate-code');
const executeButton = el<HTMLButtonElement>('execute-code');
const fileInput = el<HTMLInputElement>('code-file');
const sourceSelect = el<HTMLSelectElement>('data-source');
const MAX_CODE_BYTES = 64 * 1024;
const templates = {
  'mean-reversion': `def on_bar(ctx):
    history = ctx["history"]
    if len(history) < 10:
        return {"action": "hold"}

    average = sum(bar["close"] for bar in history[-10:]) / 10
    price = ctx["bar"]["close"]
    shares = ctx["portfolio"]["shares"]

    if shares == 0 and price < average * 0.98:
        return {"action": "buy", "quantity": 1,
                "reason": "최근 10봉 평균보다 2% 낮아 1주 매수"}
    if shares > 0 and price > average * 1.02:
        return {"action": "sell", "quantity": shares,
                "reason": "최근 10봉 평균보다 2% 높아 보유분 매도"}
    return {"action": "hold"}
`,
  empty: `def on_bar(ctx):
    # ctx: bar, history, portfolio(cash, shares), index
    # 여기에 나의 진입·청산 조건을 작성하세요.
    return {"action": "hold"}
`,
};
let current: Report | undefined;
let chartBaseline: Report | undefined;
type ReportView = 'summary' | 'analysis' | 'audit';
const reportViews: ReportView[] = ['summary', 'analysis', 'audit'];
const viewDescriptions = { summary: '핵심 성과부터 살펴보세요.', analysis: '자산 변화와 손실 구간을 먼저 살펴보세요.', audit: '거래를 선택해 신호와 체결 근거를 확인하세요.' };
function selectReportView(view: ReportView, remember = false) {
  const report = el('report');
  report.dataset.view = view;
  el('view-description').textContent = viewDescriptions[view];
  document.querySelectorAll<HTMLButtonElement>('[data-report-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.reportView === view)));
  const main = view === 'analysis' ? ['performance-panel', 'metrics', 'transactions-panel']
    : view === 'audit' ? ['transactions-panel', 'metrics', 'performance-panel']
    : ['metrics', 'performance-panel', 'transactions-panel'];
  // Move existing nodes so focus/reading order follows the visible layout.
  // Report data, selected trade and chart position remain unchanged.
  const scrolls = Array.from(report.querySelectorAll<HTMLElement>('.table-scroll')).map(node => ({node, top: node.scrollTop, left: node.scrollLeft}));
  const ordered = main.flatMap(id => id === 'performance-panel' ? [id, 'comparison'] : [id]);
  for (const id of [...ordered, 'methodology-panel']) report.append(el(id));
  scrolls.forEach(({node, top, left}) => { node.scrollTop = top; node.scrollLeft = left; });
  if (remember) { try { localStorage.setItem('meritz-report-view', view); } catch { /* Embedded hosts may disallow storage. */ } }
}
let initialView: ReportView = 'summary';
try { const saved = localStorage.getItem('meritz-report-view'); if (reportViews.includes(saved as ReportView)) initialView = saved as ReportView; } catch { /* In-memory selection still works. */ }
selectReportView(initialView);
document.querySelectorAll<HTMLButtonElement>('[data-report-view]').forEach(button => button.addEventListener('click', () => selectReportView(button.dataset.reportView as ReportView, true)));

let baselineRunId: string | undefined;
let connected = false;
let busy = false;
let generation = 0;
const money = (value: number) => {
  // Keep small simulated costs visible instead of rounding them to zero won.
  if (value !== 0 && Math.abs(value) < 0.01) return value > 0 ? '0.01원 미만' : '-0.01원 미만';
  return `${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: Math.abs(value) < 100 ? 2 : 0 }).format(value)}원`;
};
const rounded = (value: number) => Math.abs(value) < 0.005 ? 0 : value;
const tone = (value: number) => rounded(value) === 0 ? '' : value > 0 ? 'positive' : 'negative';
const pct = (value: number) => `${rounded(value) > 0 ? '+' : ''}${rounded(value).toFixed(2)}%`;

function node<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, className?: string) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}

function setBusy(value: boolean, message?: string) {
  busy = value;
  runButton.disabled = value || !connected;
  compareButton.disabled = value || !connected || !current;
  validateButton.disabled = value || !connected || preview;
  executeButton.disabled = value || !connected || preview;
  fileInput.disabled = value;
  el<HTMLSelectElement>('code-template').disabled = value;
  codeEditor.disabled = value;
  document.querySelectorAll<HTMLButtonElement>('.trade-button').forEach(button => { button.disabled = value; });
  el('report').setAttribute('aria-busy', String(value));
  if (message !== undefined) el('status').textContent = message;
}

function showError(message: string) {
  el('error').textContent = message;
  el('error').hidden = false;
}

function clearError() { el('error').hidden = true; }

async function callTool(name: string, args: Record<string, unknown>): Promise<Result> {
  if (!local) return app.callServerTool({ name, arguments: args });
  if (!localToken) throw new Error('로컬 실행 토큰이 없습니다. 검증 서버가 제공한 주소로 열어 주세요.');
  const response = await fetch('/api/backtest', {
    method: 'POST', credentials: 'omit', headers: { 'Content-Type': 'application/json', 'X-Backtest-Token': localToken },
    body: JSON.stringify({ name, arguments: args }), signal: AbortSignal.timeout(95000),
  });
  if (!response.ok) throw new Error(`로컬 실행 요청이 거부되었습니다 (${response.status}).`);
  return await response.json() as Result;
}

function strategyLabel(strategy: Report['strategy']) {
  return 'interface' in strategy ? strategy.name : `이동평균 교차 · ${strategy.fast} / ${strategy.slow}`;
}

function toolError(result: Result, fallback: string): string {
  const text = result.content?.filter((item): item is {type: string; text: string} =>
    !!item && typeof item === 'object' && (item as {type?:unknown}).type === 'text'
    && typeof (item as {text?:unknown}).text === 'string').map(item => item.text).join('\n');
  return text ? text.slice(0, 1000) : fallback;
}

function isReport(value: unknown): value is Report {
  if (!value || typeof value !== 'object') return false;
  const r = value as Report;
  const finite = (n: unknown) => typeof n === 'number' && Number.isFinite(n);
  return r.schemaVersion === 1 && typeof r.runId === 'string'
    && ['synthetic', 'meritz'].includes(r.source?.kind) && typeof r.source.label === 'string'
    && !!r.strategy && ('interface' in r.strategy
      ? r.strategy.interface === 'on_bar_v1' && typeof r.strategy.name === 'string' && typeof r.strategy.codeHash === 'string'
      : finite(r.strategy.fast) && finite(r.strategy.slow))
    && !!r.metrics && ['totalReturnPct', 'maxDrawdownPct', 'benchmarkReturnPct', 'tradeCount', 'finalEquity', 'totalFees'].every(k => finite(r.metrics[k as keyof Report['metrics']]))
    && !!r.assumptions && finite(r.assumptions.initialCash) && finite(r.assumptions.feeBps) && finite(r.assumptions.slippageBps) && typeof r.assumptions.fillRule === 'string'
    && Array.isArray(r.equity) && r.equity.length > 0 && r.equity.length <= 5000
    && r.equity.every(p => typeof p.date === 'string' && finite(p.equity) && finite(p.benchmark) && finite(p.drawdownPct))
    && Array.isArray(r.trades) && r.trades.length <= 5000 && r.trades.every(isTrade)
    && Array.isArray(r.warnings) && r.warnings.length <= 100 && r.warnings.every(w => typeof w === 'string');
}

function isTrade(value: unknown): value is Trade {
  if (!value || typeof value !== 'object') return false;
  const t = value as Trade;
  return typeof t.id === 'string' && typeof t.date === 'string' && typeof t.signalDate === 'string'
    && (t.side === 'BUY' || t.side === 'SELL') && [t.quantity, t.price, t.fee].every(Number.isFinite)
    && typeof t.reason === 'string';
}

function handleResult(result: Result) {
  if (result.isError) throw new Error(toolError(result, '도구 실행에 실패했습니다. 조건을 확인하고 다시 실행해 주세요.'));
  const data = result.structuredContent && typeof result.structuredContent === 'object'
    ? result.structuredContent as Record<string, unknown> : undefined;
  if (isReport(data?.report)) {
    render(data.report);
  } else if (isReport(data?.baseline) && isReport(data?.candidate)) {
    render(data.candidate, { baseline: data.baseline, candidate: data.candidate });
  } else {
    throw new Error('지원하지 않는 결과 형식입니다. MCP 서버와 보고서 버전을 확인해 주세요.');
  }
}

function metric(label: string, value: string, note: string, tone = '') {
  const card = node('div', undefined, 'metric-card');
  card.append(node('div', label, 'metric-label'), node('div', value, `metric-value ${tone}`), node('div', note, 'metric-note'));
  return card;
}

function render(report: Report, comparison?: Comparison) {
  generation++;
  current = report;
  el<HTMLDetailsElement>('code-workspace').open = false;
  baselineRunId = comparison?.baseline.runId ?? report.runId;
  const synthetic = report.source.kind === 'synthetic';
  runButton.hidden = !synthetic || 'interface' in report.strategy;
  compareButton.hidden = 'interface' in report.strategy;
  el('source-title').textContent = synthetic ? '합성 시세로 실행하는 데모' : '고객 키로 조회한 메리츠 과거 시세';
  el('source-description').textContent = synthetic
    ? '실제 종목의 투자 성과가 아닙니다. 이 데모 실행은 고객 API 키를 사용하지 않습니다.'
    : '과거 시세에 규칙을 적용한 가상 체결 결과입니다. 실제 계좌 수익이나 실주문 결과가 아닙니다.';
  el('version-badge').textContent = synthetic ? 'MCP APP · DEMO' : 'MCP APP · HISTORICAL';
  el('report').hidden = false;
  el('strategy-title').textContent = strategyLabel(report.strategy);
  el('run-meta').textContent = `${report.source.label} · 실행 ${report.runId.slice(0, 12)}`;
  const m = report.metrics;
  const excess = m.totalReturnPct - m.benchmarkReturnPct;
  const facts = el('report-facts');
  facts.replaceChildren();
  for (const [label, value] of [
    ['분석 기간', `${report.equity[0].date} — ${report.equity.at(-1)!.date}`],
    ['관측 봉', `${report.equity.length}개`],
    ['가상 체결', `${m.tradeCount}건`],
    ['누적 수수료', money(m.totalFees)],
  ]) {
    const fact = node('div'); fact.append(node('span', label), node('strong', value)); facts.append(fact);
  }
  el('metrics').replaceChildren(
    metric('총 수익률', pct(m.totalReturnPct), '종료 시 보유 자산 평가손익 포함', tone(m.totalReturnPct)),
    metric('최대 낙폭', `${Math.abs(m.maxDrawdownPct).toFixed(2)}%`, '고점 대비 최대 자산 감소율', tone(-Math.abs(m.maxDrawdownPct))),
    metric('단순 보유 대비', `${pct(excess)}p`, `단순 보유 수익률 ${pct(m.benchmarkReturnPct)}`, tone(excess)),
    metric('종료 자산', money(m.finalEquity), `시작 자금 ${money(report.assumptions.initialCash)}`),
  );
  chartBaseline = comparison?.baseline;
  renderChart(report, chartBaseline);
  renderComparison(comparison);
  renderTrades(report);
  el('trade-detail').replaceChildren(node('p', '거래를 선택하면 엔진에 기록된 신호와 체결 내역을 확인할 수 있습니다.', 'empty-state'));
  const assumptions = el('assumptions');
  assumptions.replaceChildren();
  for (const [key, value] of [
    ['시작 자금', money(report.assumptions.initialCash)],
    ['체결 기준', report.assumptions.fillRule],
    ['슬리피지', `${report.assumptions.slippageBps}bp`],
    ['수수료율', `${report.assumptions.feeBps}bp / 체결`],
    ['종료 자산', money(m.finalEquity)],
    ['데이터 출처', report.source.label],
    ['비교 기준', '동일 시세의 단순 보유'],
  ]) {
    const group = node('div'); group.append(node('dt', key), node('dd', value)); assumptions.append(group);
  }
  el('warnings').replaceChildren(...report.warnings.map(w => node('li', w)));
  el('status').textContent = comparison ? '동일 데이터로 두 전략을 계산했습니다. 아래에서 차이를 확인하세요.' : '계산이 완료되었습니다. 거래를 선택해 근거를 확인하세요.';
}

function renderChart(report: Report, baseline?: Report, selectedIndex = report.equity.length - 1) {
  const ns = 'http://www.w3.org/2000/svg';
  const svgNode = (tag: string, attrs: Record<string, string | number> = {}, text?: string) => {
    const item = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attrs)) item.setAttribute(key, String(value));
    if (text !== undefined) item.textContent = text;
    return item;
  };
  const width = Math.max(220, el('chart').clientWidth);
  el('report').style.setProperty('--strategy-color', tone(report.metrics.totalReturnPct) === 'positive' ? 'var(--up)' : tone(report.metrics.totalReturnPct) === 'negative' ? 'var(--down)' : 'var(--ink)');
  const left = 60, right = width - 16, top = 25, bottom = width < 600 ? 210 : 270;
  const svg = svgNode('svg', { viewBox: `0 0 ${width} ${bottom + 40}`, role: 'img', 'aria-label': '전략과 단순 보유의 일자별 자산 곡선' });
  const series = [report.equity.map(p => p.equity), report.equity.map(p => p.benchmark)];
  if (baseline) series.push(baseline.equity.map(p => p.equity));
  const values = series.flat();
  const min = Math.min(...values), max = Math.max(...values);
  const spread = Math.max(max - min, Math.abs(max) * .02, 1);
  const low = min - spread * .12, high = max + spread * .12;
  const y = (v: number) => bottom - (v - low) / (high - low) * (bottom - top);
  const x = (i: number, length = report.equity.length) => left + i / Math.max(1, length - 1) * (right - left);
  const defs = svgNode('defs');
  const gradient = svgNode('linearGradient', { id: 'equity-fill', x1: '0', y1: '0', x2: '0', y2: '1' });
  gradient.append(svgNode('stop', { offset: '0%', 'stop-color': 'var(--strategy-color)', 'stop-opacity': '.16' }), svgNode('stop', { offset: '100%', 'stop-color': 'var(--strategy-color)', 'stop-opacity': '0' }));
  defs.append(gradient); svg.append(defs);
  for (let i = 0; i < 5; i++) {
    const value = low + (high - low) * i / 4;
    svg.append(svgNode('line', { x1: left, x2: right, y1: y(value), y2: y(value), stroke: 'var(--border)', 'stroke-width': 1, 'stroke-dasharray': '3 5' }),
      svgNode('text', { x: 48, y: y(value) + 4, 'text-anchor': 'end', fill: 'var(--muted)', 'font-size': 11 }, `${(value / 10000).toFixed(Math.abs(value) < 100000 ? 1 : 0)}만`));
  }
  const strategyPoints = series[0].map((v, i) => `${x(i)},${y(v)}`).join(' ');
  svg.append(svgNode('polygon', { points: `${left},${bottom} ${strategyPoints} ${right},${bottom}`, fill: 'url(#equity-fill)' }));
  for (const index of [1, 2, 0]) {
    const points = series[index]; if (!points) continue;
    const line = svgNode('polyline', { points: points.map((v, i) => `${x(i, points.length)},${y(v)}`).join(' '), fill: 'none', stroke: ['var(--strategy-color)', '#a1aeb7', '#8770ae'][index], 'stroke-width': index === 0 ? 3 : 1.8, 'stroke-linejoin': 'round' });
    if (index === 1) line.setAttribute('stroke-dasharray', '5 5');
    svg.append(line);
  }
  const end = report.equity.at(-1)!;
  svg.append(svgNode('circle', { cx: right, cy: y(end.equity), r: 4, fill: 'var(--strategy-color)', stroke: 'var(--surface)', 'stroke-width': 2 }));
  for (const fraction of (width < 500 ? [0, .5, 1] : [0, .25, .5, .75, 1])) {
    const index = Math.round((report.equity.length - 1) * fraction);
    svg.append(svgNode('text', { x: x(index), y: bottom + 28, 'text-anchor': fraction === 0 ? 'start' : fraction === 1 ? 'end' : 'middle', fill: 'var(--muted)', 'font-size': 11 }, report.equity[index].date.slice(5)));
  }
  const cursor = svgNode('line', { x1: right, x2: right, y1: top, y2: bottom, stroke: '#6e8f95', 'stroke-width': 1, 'stroke-dasharray': '3 3' });
  svg.append(cursor);
  el('chart').replaceChildren(svg);
  el('comparison-legend').hidden = !baseline;
  el('chart-caption').replaceChildren(node('span', '기준: 일별 종가 평가 · 단위: 원'), node('span', `종료 자산 ${money(end.equity)}`));

  const dd = svgNode('svg', { viewBox: `0 0 ${width} 108`, role: 'img', 'aria-label': '일자별 고점 대비 낙폭, 아래쪽으로 갈수록 손실이 큼' });
  const drawdowns = report.equity.map(p => Math.abs(p.drawdownPct));
  const worst = Math.max(...drawdowns);
  const ddRange = Math.max(worst * 1.12, 1);
  const dy = (value: number) => 12 + value / ddRange * 74;
  dd.append(svgNode('line', { x1: left, x2: right, y1: 12, y2: 12, stroke: 'var(--border)' }));
  dd.append(svgNode('polygon', { points: `${left},12 ${drawdowns.map((v, i) => `${x(i)},${dy(v)}`).join(' ')} ${right},12`, fill: 'var(--down)', 'fill-opacity': '.14' }));
  dd.append(svgNode('polyline', { points: drawdowns.map((v, i) => `${x(i)},${dy(v)}`).join(' '), stroke: 'var(--down)', 'stroke-width': 1.5, fill: 'none' }));
  dd.append(svgNode('text', { x: 48, y: 16, 'text-anchor': 'end', fill: 'var(--muted)', 'font-size': 11 }, '0%'), svgNode('text', { x: 48, y: 86, 'text-anchor': 'end', fill: 'var(--muted)', 'font-size': 11 }, `−${ddRange.toFixed(1)}%`));
  const ddCursor = svgNode('line', { x1: right, x2: right, y1: 12, y2: 86, stroke: '#6e8f95', 'stroke-dasharray': '3 3' });
  dd.append(ddCursor);
  el('drawdown-chart').replaceChildren(dd);
  const worstIndex = drawdowns.indexOf(worst);
  el('drawdown-summary').textContent = worst > 0 ? `최대 ${worst.toFixed(2)}% · ${report.equity[worstIndex].date}` : '관측 구간 내 낙폭 0%';
  const slider = el<HTMLInputElement>('chart-position');
  slider.max = String(report.equity.length - 1); slider.value = String(selectedIndex);
  const inspect = (index: number) => {
    const point = report.equity[index];
    el('chart-point').textContent = `${point.date} · 자산 ${money(point.equity)} · 낙폭 ${Math.abs(point.drawdownPct).toFixed(2)}%`;
    slider.setAttribute('aria-valuetext', el('chart-point').textContent!);
    for (const line of [cursor, ddCursor]) { line.setAttribute('x1', String(x(index))); line.setAttribute('x2', String(x(index))); }
  };
  slider.oninput = () => inspect(Number(slider.value));
  for (const chart of [svg, dd]) chart.addEventListener('pointermove', event => {
    const bounds = chart.getBoundingClientRect();
    const pointer = event as PointerEvent;
    const index = Math.round(Math.max(0, Math.min(1, ((pointer.clientX - bounds.left) / bounds.width * width - left) / (right - left))) * (report.equity.length - 1));
    slider.value = String(index); inspect(index);
  });
  inspect(selectedIndex);
}

let lastChartWidth = 0;
new ResizeObserver(() => {
  const width = el('chart').clientWidth;
  if (!current || width < 1 || width === lastChartWidth) return;
  lastChartWidth = width;
  renderChart(current, chartBaseline, Math.min(current.equity.length - 1, Number(el<HTMLInputElement>('chart-position').value)));
}).observe(el('chart'));

function renderComparison(comparison?: Comparison) {
  el('comparison').hidden = !comparison;
  if (!comparison) return;
  const { baseline: a, candidate: b } = comparison;
  const table = node('table');
  const head = node('thead'), header = node('tr');
  for (const text of ['지표', `기본 ${strategyLabel(a.strategy)}`, `변경 ${strategyLabel(b.strategy)}`, '변화']) header.append(node('th', text));
  head.append(header); const body = node('tbody');
  for (const values of [
    ['총 수익률', pct(a.metrics.totalReturnPct), pct(b.metrics.totalReturnPct), `${(b.metrics.totalReturnPct - a.metrics.totalReturnPct).toFixed(2)}%p`],
    ['최대 낙폭', `${Math.abs(a.metrics.maxDrawdownPct).toFixed(2)}%`, `${Math.abs(b.metrics.maxDrawdownPct).toFixed(2)}%`, `${(Math.abs(b.metrics.maxDrawdownPct) - Math.abs(a.metrics.maxDrawdownPct)).toFixed(2)}%p`],
    ['가상 체결', `${a.metrics.tradeCount}건`, `${b.metrics.tradeCount}건`, `${b.metrics.tradeCount - a.metrics.tradeCount}건`],
    ['수수료', money(a.metrics.totalFees), money(b.metrics.totalFees), money(b.metrics.totalFees - a.metrics.totalFees)],
  ]) { const row = node('tr'); for (const value of values) row.append(node('td', value)); body.append(row); }
  table.append(head, body); const scroll = node('div', undefined, 'table-scroll'); scroll.append(table);
  el('comparison-content').replaceChildren(scroll);
}

function renderTrades(report: Report) {
  el('trade-count').textContent = `${report.trades.length}건`;
  const body = el('trades'); body.replaceChildren();
  if (!report.trades.length) {
    const row = node('tr'), cell = node('td', '이 조건에서 체결된 거래가 없습니다.'); cell.colSpan = 5; row.append(cell); body.append(row); return;
  }
  for (const trade of report.trades) {
    const row = node('tr'); row.dataset.tradeId = trade.id;
    row.append(node('td', trade.date), node('td', trade.side === 'BUY' ? '매수' : '매도', trade.side === 'BUY' ? 'buy' : 'sell'), node('td', `${trade.quantity}주`, 'numeric'), node('td', money(trade.price), 'numeric'));
    const cell = node('td'), button = node('button', '근거 보기', 'trade-button');
    button.type = 'button'; button.setAttribute('aria-label', `${trade.date} ${trade.side === 'BUY' ? '매수' : '매도'} 거래 근거 보기`);
    button.addEventListener('click', () => { void showTrade(trade.id); }); cell.append(button); row.append(cell); body.append(row);
  }
}

function renderTrade(trade: Trade) {
  const details = node('dl');
  for (const [key, value] of [
    ['거래', trade.side === 'BUY' ? '매수' : '매도'], ['신호일', trade.signalDate], ['체결일', trade.date],
    ['수량', `${trade.quantity}주`], ['체결 가격', money(trade.price)], ['수수료', money(trade.fee)],
  ]) { const row = node('div', undefined, 'detail-row'); row.append(node('dt', key), node('dd', value)); details.append(row); }
  const custom = current && 'interface' in current.strategy;
  el('trade-detail').replaceChildren(node('p', custom ? '전략 코드가 기록한 사유' : '엔진이 기록한 거래 조건', 'eyebrow'), node('p', trade.reason, 'trade-reason'), details, node('p', custom ? '사유는 사용자 코드가 반환한 문구이며, 엔진이 그 설명의 타당성을 검증한 것은 아닙니다. 체결 수량·가격은 엔진 계산 결과입니다.' : '이 설명은 엔진에 기록된 거래 조건입니다. 실제 시장에서 같은 가격에 체결된다는 의미는 아닙니다.', 'footnote'));
  document.querySelectorAll<HTMLElement>('#trades tr').forEach(row => row.classList.toggle('selected-trade', row.dataset.tradeId === trade.id));
}

async function showTrade(tradeId: string) {
  if (busy || !current) return;
  const report = current, token = generation;
  clearError(); setBusy(true, '거래 근거를 조회하고 있습니다…');
  try {
    let trade: unknown;
    if (preview) trade = report.trades.find(t => t.id === tradeId);
    else {
      const result = await callTool('meritz_backtest_trade', { runId: report.runId, tradeId });
      if (result.isError) throw new Error(toolError(result, '거래 근거를 조회하지 못했습니다. 대화에서 다시 실행한 뒤 시도해 주세요.'));
      const payload = result.structuredContent;
      trade = payload && typeof payload === 'object' ? (payload as Record<string, unknown>).trade : undefined;
    }
    if (generation !== token) return;
    if (!isTrade(trade) || trade.id !== tradeId) throw new Error('올바른 거래 상세를 받지 못했습니다.');
    renderTrade(trade); el('status').textContent = `${trade.date} 거래 근거를 표시했습니다.`;
  } catch (error) { if (generation === token) showError(error instanceof Error ? error.message : '거래 조회 오류가 발생했습니다.'); }
  finally { if (generation === token) setBusy(false); }
}

async function execute(compare: boolean) {
  if (busy || !connected) return;
  if (!compare && current?.source.kind === 'meritz') return;
  clearError(); setBusy(true, compare ? '같은 시세로 기본 전략과 변경 전략을 계산하고 있습니다…' : '기본 전략을 계산하고 있습니다…');
  const token = generation;
  try {
    if (preview) {
      const data = await import('./preview-data');
      if (generation !== token) return;
      handleResult({ structuredContent: compare ? data.compareReports : { report: data.demoReport } });
      setBusy(false);
    } else {
      const result = compare
        ? await callTool('meritz_backtest_compare', { runId: baselineRunId, fast: 8, slow: 30 })
        : await callTool('meritz_backtest_demo', { fast: 5, slow: 20 });
      if (generation !== token) return;
      handleResult(result);
      setBusy(false);
    }
  } catch (error) {
    if (generation === token) {
      showError(error instanceof Error ? error.message : '실행하지 못했습니다. 다시 시도해 주세요.');
      el('status').textContent = '실행 실패 · 버튼을 눌러 다시 시도할 수 있습니다.';
    }
  }
  finally { if (generation === token) setBusy(false); }
}

function codeArguments(): Record<string, unknown> {
  const code = codeEditor.value;
  if (!code.trim()) throw new Error('실행할 Python 코드를 입력해 주세요.');
  if (new TextEncoder().encode(code).byteLength > MAX_CODE_BYTES) throw new Error('코드는 UTF-8 기준 64KB 이하여야 합니다.');
  return { code };
}

function runArguments(): Record<string, unknown> {
  const args = codeArguments();
  const source = sourceSelect.value;
  if (!['demo', 'meritz'].includes(source)) throw new Error('시세 출처를 선택해 주세요.');
  const symbol = el<HTMLInputElement>('symbol').value.trim();
  const startDate = el<HTMLInputElement>('start-date').value;
  const endDate = el<HTMLInputElement>('end-date').value;
  const account = el<HTMLInputElement>('account').value.trim();
  if (source === 'meritz') {
    if (!/^\d{6}$/.test(symbol)) throw new Error('종목 코드는 숫자 6자리로 입력해 주세요.');
    if (!startDate || !endDate || startDate > endDate) throw new Error('시작일과 종료일을 올바르게 선택해 주세요.');
  }
  return { ...args, source, ...(source === 'meritz' ? { symbol, startDate, endDate, ...(account ? { account } : {}) } : {}), initialCash: 1000000, feeBps: 15, slippageBps: 10 };
}

function renderValidation(result: Result) {
  if (result.isError) throw new Error(toolError(result, '코드 검증을 완료하지 못했습니다.'));
  const data = result.structuredContent as { valid?: unknown; errors?: unknown[]; interface?: unknown; limits?: unknown } | undefined;
  if (!data || typeof data.valid !== 'boolean' || !Array.isArray(data.errors)) throw new Error('올바른 코드 검증 결과를 받지 못했습니다.');
  const output = el('validation-result');
  output.hidden = false;
  output.classList.toggle('validation-failed', !data.valid);
  const heading = node('strong', data.valid ? '코드 검증 통과 · 아직 실행하지 않았습니다' : '코드를 수정해 주세요');
  const errors = node('ul');
  for (const error of data.errors.slice(0, 30)) errors.append(node('li', typeof error === 'string' ? error : JSON.stringify(error)));
  const detail = node('p', data.valid ? '문법·허용 규칙 검증 결과입니다. 실행 중 오류나 전략의 수익성을 보장하지 않습니다.' : '아래 진단을 참고해 코드를 수정한 뒤 다시 검증할 수 있습니다.', 'footnote');
  output.replaceChildren(heading, detail, errors);
}

async function executeCode(validateOnly: boolean) {
  if (busy || !connected || preview) return;
  clearError();
  let args: Record<string, unknown>;
  try { args = validateOnly ? codeArguments() : runArguments(); }
  catch (error) { showError(error instanceof Error ? error.message : '입력값을 확인해 주세요.'); return; }
  const token = generation;
  setBusy(true, validateOnly ? '내 코드의 문법과 지원 규칙을 확인하고 있습니다…' : '내 코드를 실행하고 있습니다. 제한 시간을 넘으면 중단됩니다…');
  try {
    const result = await callTool(validateOnly ? 'meritz_backtest_validate' : 'meritz_backtest_code', args);
    if (generation !== token) return;
    if (validateOnly) { renderValidation(result); el('status').textContent = '코드 검증 결과를 확인하세요.'; }
    else { handleResult(result); setBusy(false); }
  } catch (error) {
    if (generation === token) { showError(error instanceof Error ? error.message : '코드 실행을 완료하지 못했습니다.'); el('status').textContent = '실행하지 못했습니다. 코드를 수정하거나 다시 시도해 주세요.'; }
  } finally { if (generation === token) setBusy(false); }
}

codeEditor.value = templates['mean-reversion'];
const end = new Date(); end.setDate(end.getDate() - 1);
const startDate = new Date(end); startDate.setDate(startDate.getDate() - 180);
el<HTMLInputElement>('end-date').value = end.toISOString().slice(0, 10);
el<HTMLInputElement>('start-date').value = startDate.toISOString().slice(0, 10);
el<HTMLSelectElement>('code-template').addEventListener('change', event => {
  const selected = (event.target as HTMLSelectElement).value as keyof typeof templates;
  if (templates[selected]) { codeEditor.value = templates[selected]; el('validation-result').hidden = true; el('file-status').textContent = '템플릿을 불러왔습니다. 내 조건에 맞게 수정하세요.'; }
});
codeEditor.addEventListener('input', () => { el('validation-result').hidden = true; });
fileInput.addEventListener('change', async () => {
  const file = fileInput.files?.[0];
  if (!file) return;
  clearError();
  try {
    if (!file.name.toLowerCase().endsWith('.py')) throw new Error('.py 파일을 선택해 주세요.');
    if (file.size > MAX_CODE_BYTES) throw new Error('파일은 64KB 이하여야 합니다.');
    const code = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
    if (busy) throw new Error('실행이 끝난 뒤 파일을 다시 선택해 주세요.');
    codeEditor.value = code; el('validation-result').hidden = true;
    el('file-status').textContent = `${file.name} · ${(file.size / 1024).toFixed(1)}KB · 아직 실행하지 않음`;
  } catch (error) { showError(error instanceof Error ? error.message : 'UTF-8 Python 파일을 읽지 못했습니다.'); }
  finally { fileInput.value = ''; }
});
sourceSelect.addEventListener('change', () => {
  const demo = sourceSelect.value === 'demo';
  for (const id of ['symbol', 'start-date', 'end-date', 'account']) el<HTMLInputElement>(id).disabled = demo;
  el('execution-help').textContent = demo ? '합성 시세로 코드 동작만 시험합니다. 실제 종목의 성과가 아니며 API 키를 사용하지 않습니다.' : '등록된 고객 API 키를 시세 조회에 사용합니다. 이 화면에 키를 입력하지 않습니다.';
});
validateButton.addEventListener('click', () => { void executeCode(true); });
executeButton.addEventListener('click', () => { void executeCode(false); });

runButton.addEventListener('click', () => { void execute(false); });
compareButton.addEventListener('click', () => { void execute(true); });
app.ontoolinput = () => {
  // A host-initiated run supersedes pending UI requests. Their late results and
  // finally blocks must not replace this run or release its loading state.
  generation++;
  clearError();
  setBusy(true, 'Claude가 요청한 전략 결과를 계산하고 있습니다…');
};
app.ontoolresult = result => {
  try { clearError(); handleResult(result); }
  catch (error) { showError(error instanceof Error ? error.message : '결과를 표시하지 못했습니다.'); }
  finally { setBusy(false); }
};
app.ontoolcancelled = () => { generation++; setBusy(false, '실행이 취소되었습니다. 다시 실행할 수 있습니다.'); };
app.onhostcontextchanged = context => { if (context.theme) document.documentElement.dataset.theme = context.theme; };
app.onerror = () => { showError('Claude와의 연결 오류가 발생했습니다. 대화에서 보고서 도구를 다시 실행해 주세요.'); };

async function start() {
  if (preview) {
    el('preview-notice').hidden = false; connected = true; setBusy(false); await execute(false);
    el('execution-help').textContent = '화면 미리보기에서는 코드를 실행하거나 검증하지 않습니다. Claude MCP 또는 로컬 검증 서버에서 실행해 주세요.';
    return;
  }
  if (local) {
    el('preview-notice').hidden = false;
    el('preview-notice').textContent = '로컬 실행 화면 · Claude 연결 아님';
    if (!localToken || !['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname)) {
      setBusy(false, '로컬 실행 연결 실패');
      showError('인증된 로컬 검증 서버가 제공한 주소로 열어 주세요. 미리보기로 자동 전환하지 않습니다.');
      return;
    }
    connected = true; setBusy(false, '로컬 실행 준비 완료 · 코드와 시세 출처를 확인한 뒤 실행해 주세요.');
    return;
  }
  try {
    await app.connect(); connected = true;
    const context = app.getHostContext();
    if (context?.theme) document.documentElement.dataset.theme = context.theme;
    setBusy(busy, busy ? undefined : current ? '결과가 준비되었습니다.' : 'Claude에 연결되었습니다. 도구 실행 결과를 기다리고 있습니다.');
  } catch {
    connected = false; setBusy(false, 'MCP 연결을 완료하지 못했습니다.');
    showError('이 화면은 MCP Apps를 지원하는 Claude에서 열어야 합니다. 로컬 미리보기는 명시적으로 ?preview=1을 사용해 주세요.');
  }
}
void start();
