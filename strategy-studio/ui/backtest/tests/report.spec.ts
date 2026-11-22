import { test, expect } from '@playwright/test';
import { demoReport, compareReports } from '../src/preview-data';

test('preview has visible provenance, trade drilldown, comparison, narrow layout', async ({page}) => {
  await page.goto('/mcp-app.html?preview=1');
  await expect(page.locator('#preview-notice')).toBeVisible();
  await expect(page.locator('#compare-button')).toBeEnabled();
  await page.locator('.trade-button').first().click();
  await expect(page.locator('#trade-detail')).toContainText('SMA');
  await page.locator('#compare-button').click();
  await expect(page.locator('#comparison')).toBeVisible();
  await page.screenshot({path:'test-results/report-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({path:'test-results/report-mobile.png',fullPage:true});
});

test('MCP host handshake then initial result enables comparison and tools', async ({page}) => {
  const data = {demoReport, compareReports};
  await page.route('**/host', route => route.fulfill({contentType:'text/html',body:`<!doctype html><html><body>
  <script>
  const fixtures = ${JSON.stringify(data)};
  window.compared = []; window.toolCalls = 0;
  window.notify = (method,params)=>document.querySelector('iframe').contentWindow.postMessage({jsonrpc:'2.0',method,params},location.origin);
  addEventListener('message',event=>{
    const msg=event.data; if(!msg || msg.jsonrpc!=='2.0')return;
    let result;
    if(msg.method==='ui/initialize')result={protocolVersion:'2026-01-26',hostInfo:{name:'Test host',version:'1'},hostCapabilities:{serverTools:{}},hostContext:{theme:'light',displayMode:'inline'}};
    if(msg.method==='tools/call') {
      window.toolCalls++;
      const p=msg.params;
      if(p.name==='meritz_backtest_compare')window.compared.push(p.arguments.runId);
      result={content:[],structuredContent:p.name==='meritz_backtest_compare'?fixtures.compareReports:p.name==='meritz_backtest_trade'?{trade:fixtures.demoReport.trades.find(t=>t.id===p.arguments.tradeId)}:{report:fixtures.demoReport}};
    }
    if(result)event.source.postMessage({jsonrpc:'2.0',id:msg.id,result},event.origin);
  });
  </script><iframe title="report" src="/mcp-app.html" style="width:100%;height:1000px"></iframe></body></html>`}));
  await page.goto('/host');
  const frame=page.frameLocator('iframe');
  await expect(frame.locator('#status')).toContainText('연결되었습니다');
  await expect(frame.locator('#compare-button')).toBeDisabled();
  await page.evaluate(()=> (window as any).notify('ui/notifications/tool-input',{arguments:{fast:5,slow:20}}));
  await expect(frame.locator('#status')).toContainText('계산하고 있습니다');
  await page.evaluate(report=>(window as any).notify('ui/notifications/tool-result',{content:[],structuredContent:{report}}),demoReport);
  await expect(frame.locator('#compare-button')).toBeEnabled();
  const callsBefore = await page.evaluate(()=>(window as any).toolCalls);
  for (const view of ['summary','analysis','audit']) await frame.locator(`[data-report-view="${view}"]`).click();
  expect(await page.evaluate(()=>(window as any).toolCalls)).toBe(callsBefore);
  await frame.locator('.trade-button').first().click();
  await expect(frame.locator('#trade-detail')).toContainText('SMA');
  await frame.locator('#compare-button').click();
  await expect(frame.locator('#comparison')).toBeVisible();
  await frame.locator('#compare-button').click();
  await expect.poll(()=>page.evaluate(()=>(window as any).compared)).toEqual([demoReport.runId,demoReport.runId]);
  // Mark a mocked price response as Meritz to verify source switching and avoid demo replacement.
  const actual={...demoReport,source:{kind:'meritz',label:'모의 메리츠 응답',symbol:'005930',market:'TJ',startDate:'2025-01-02',endDate:'2025-06-30',fetchedAt:'2026-01-01'}};
  await page.evaluate(report=>(window as any).notify('ui/notifications/tool-result',{content:[],structuredContent:{report}}),actual);
  await expect(frame.locator('#run-button')).toBeHidden();
  await expect(frame.locator('#source-title')).toContainText('메리츠');
  // Host strings must be text, not interpreted markup.
  const poisoned={...demoReport,source:{kind:'synthetic',label:'<img src=x onerror="window.xss=1">'}};
  await page.evaluate(report=>(window as any).notify('ui/notifications/tool-result',{content:[],structuredContent:{report}}),poisoned);
  await expect(frame.locator('#run-meta')).toContainText('<img');
  await expect(frame.locator('#run-meta img')).toHaveCount(0);
  const losing={...demoReport,metrics:{...demoReport.metrics,totalReturnPct:-5},trades:Array.from({length:40},(_,i)=>({...demoReport.trades[i%demoReport.trades.length],id:'scroll-'+i}))};
  await page.evaluate(report=>(window as any).notify('ui/notifications/tool-result',{content:[],structuredContent:{report}}),losing);
  await expect.poll(()=>frame.locator('#chart polyline').last().evaluate(e=>getComputedStyle(e).stroke)).toBe('rgb(36, 100, 197)');
  const scroller=frame.locator('#transactions-panel .table-scroll');
  await scroller.evaluate(e=>{e.scrollTop=150;e.scrollLeft=0;});
  for(const view of ['summary','analysis','audit']) {
    await frame.locator(`[data-report-view="${view}"]`).click();
    expect(await scroller.evaluate(e=>e.scrollTop)).toBe(150);
  }
  const zero={...demoReport,metrics:{...demoReport.metrics,totalReturnPct:0.004,benchmarkReturnPct:0.004,maxDrawdownPct:0}};
  await page.evaluate(report=>(window as any).notify('ui/notifications/tool-result',{content:[],structuredContent:{report}}),zero);
  await expect(frame.locator('.metric-value').first()).toHaveText('0.00%');
  await expect(frame.locator('.metric-value.positive')).toHaveCount(0);
  await expect(frame.locator('.metric-value.negative')).toHaveCount(0);

});

test('three report views preserve results and selections at desktop, tablet and mobile widths', async ({page}) => {
  await page.goto('/mcp-app.html?preview=1');
  await expect(page.locator('#report')).toBeVisible();
  await page.locator('.trade-button').first().click();
  const detail = await page.locator('#trade-detail').innerText();
  const metrics = await page.locator('#metrics').innerText();
  await page.locator('#chart-position').fill('10');
  const point = await page.locator('#chart-point').innerText();
  for (const width of [1280, 768, 390, 320]) {
    await page.setViewportSize({width, height:900});
    for (const view of ['summary', 'analysis', 'audit']) {
      await page.locator(`[data-report-view="${view}"]`).click();
      await expect(page.locator('#report')).toHaveAttribute('data-view', view);
      await expect(page.locator(`[data-report-view="${view}"]`)).toHaveAttribute('aria-pressed','true');
      expect(await page.locator('#trade-detail').innerText()).toBe(detail);
      await expect(page.locator('#trades .selected-trade')).toHaveCount(1);
      const order = await page.locator('#report > [id]').evaluateAll(nodes=>nodes.map(n=>n.id));
      expect(order[1]).toBe(view === 'audit' ? 'transactions-panel' : view === 'analysis' ? 'performance-panel' : 'metrics');
      expect(order.indexOf('comparison')).toBe(order.indexOf('performance-panel')+1);
      expect(await page.locator('#metrics').innerText()).toBe(metrics);
      await expect(page.locator('#chart-point')).toHaveText(point);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
      await expect.poll(() => page.locator('#chart text').first().evaluate(e => parseFloat(getComputedStyle(e).fontSize) * (e as SVGGraphicsElement).getScreenCTM()!.a)).toBeGreaterThanOrEqual(10.9);
    }
  }
  await page.reload();
  await expect(page.locator('#report')).toHaveAttribute('data-view','audit');
  const colors = await page.locator('.negative').first().evaluate(e=>({color:getComputedStyle(e).color,down:getComputedStyle(document.documentElement).getPropertyValue('--down').trim()}));
  expect(colors.color).toBe('rgb(36, 100, 197)');
});


test('view selection works without storage; domestic colors and dark selected hover are readable', async ({page}) => {
  await page.addInitScript(() => Object.defineProperty(window, 'localStorage', {get() {throw new DOMException('denied','SecurityError');}}));
  await page.goto('/mcp-app.html?preview=1');
  await expect(page.locator('#report')).toBeVisible();
  await page.locator('[data-report-view="analysis"]').click();
  await expect(page.locator('#report')).toHaveAttribute('data-view','analysis');
  for (const selector of ['.positive', '.buy']) expect(await page.locator(selector).first().evaluate(e=>getComputedStyle(e).color)).toBe('rgb(198, 45, 67)');
  for (const selector of ['.negative', '.sell']) expect(await page.locator(selector).first().evaluate(e=>getComputedStyle(e).color)).toBe('rgb(36, 100, 197)');
  await page.evaluate(()=>document.documentElement.dataset.theme='dark');
  await page.locator('[data-report-view="analysis"]').hover();
  await expect.poll(()=>page.locator('[data-report-view="analysis"]').evaluate(e=>({bg:getComputedStyle(e).backgroundColor,text:getComputedStyle(e).color}))).toEqual({bg:'rgb(238, 242, 247)',text:'rgb(32, 40, 50)'});
});
