import { test, expect } from '@playwright/test';
import { demoReport } from '../src/preview-data';

test('customer code is loaded explicitly, validated and sent unchanged through MCP', async ({ page }) => {
  const report = { ...demoReport, strategy: { name: '내 Python 전략', codeHash: 'a'.repeat(64), interface: 'on_bar_v1' } };
  await page.route('**/code-host', route => route.fulfill({ contentType: 'text/html; charset=utf-8', body: `<!doctype html><html><head><meta charset="UTF-8"></head><body><script>
  const report=${JSON.stringify(report)};window.calls=[];
  addEventListener('message',event=>{
    const msg=event.data;if(!msg||msg.jsonrpc!=='2.0')return;let result;
    if(msg.method==='ui/initialize')result={protocolVersion:'2026-01-26',hostInfo:{name:'Test host',version:'1'},hostCapabilities:{serverTools:{}},hostContext:{theme:'light',displayMode:'inline'}};
    if(msg.method==='tools/call'){
      window.calls.push(msg.params);
      result={content:[],structuredContent:msg.params.name==='meritz_backtest_validate'?{valid:true,errors:[],interface:'on_bar_v1',limits:{maxCodeBytes:65536}}:{report}};
    }
    if(result)event.source.postMessage({jsonrpc:'2.0',id:msg.id,result},event.origin);
  });</script><iframe title="code report" src="/mcp-app.html" style="width:100%;height:1600px"></iframe></body></html>` }));
  await page.goto('/code-host');
  const frame = page.frameLocator('iframe');
  await expect(frame.locator('#execute-code')).toBeEnabled();
  await expect(frame.locator('#data-source')).toHaveValue('meritz');
  expect(await page.evaluate(() => (window as any).calls)).toEqual([]);
  const code = 'def on_bar(ctx):\n    return {"action":"hold", "reason":"사용자 파일"}\n';
  await frame.locator('#code-file').setInputFiles({ name: 'my-strategy.py', mimeType: 'text/x-python', buffer: Buffer.from(code) });
  await expect(frame.locator('#strategy-code')).toHaveValue(code);
  await expect(frame.locator('#file-status')).toContainText('아직 실행하지 않음');
  expect(await page.evaluate(() => (window as any).calls)).toEqual([]);
  await frame.locator('#validate-code').click();
  await expect(frame.locator('#validation-result')).toContainText('아직 실행하지 않았습니다');
  await frame.locator('#data-source').selectOption('demo');
  await frame.locator('#execute-code').click();
  await expect(frame.locator('#strategy-title')).toHaveText('내 Python 전략');
  await expect(frame.locator('#compare-button')).toBeHidden();
  await expect(frame.locator('#run-button')).toBeHidden();
  const calls = await page.evaluate(() => (window as any).calls);
  expect(calls.map((call: any) => call.name)).toEqual(['meritz_backtest_validate', 'meritz_backtest_code']);
  expect(calls[0].arguments).toEqual({ code });
  expect(calls[1].arguments).toMatchObject({ code, source: 'demo', initialCash: 1000000, feeBps: 15, slippageBps: 10 });
  expect(calls[1].arguments).not.toHaveProperty('path');
  expect(calls[1].arguments).not.toHaveProperty('key');
  for (const key of ['symbol', 'startDate', 'endDate', 'account']) expect(calls[1].arguments).not.toHaveProperty(key);
});

test('preview never executes customer code; oversized files are rejected without replacing editor', async ({ page }) => {
  await page.goto('/mcp-app.html?preview=1');
  await expect(page.locator('#execute-code')).toBeDisabled();
  await expect(page.locator('#validate-code')).toBeDisabled();
  await expect(page.locator('#execution-help')).toContainText('실행하거나 검증하지 않습니다');
  const original = await page.locator('#strategy-code').inputValue();
  await page.locator('#code-file').setInputFiles({ name: 'oversized.py', mimeType: 'text/x-python', buffer: Buffer.alloc(65537, 'a') });
  await expect(page.locator('#error')).toContainText('64KB');
  await expect(page.locator('#strategy-code')).toHaveValue(original);
});

test('local mode without injected token fails closed instead of falling back to demo', async ({ page }) => {
  await page.goto('/mcp-app.html?local=1');
  await expect(page.locator('#execute-code')).toBeDisabled();
  await expect(page.locator('#error')).toContainText('자동 전환하지 않습니다');
  await expect(page.locator('#report')).toBeHidden();
});
