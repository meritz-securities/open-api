import { test, expect } from '@playwright/test';

// Explicit opt-in local-server acceptance. Run with a separate config targeting
// an already running meritz.backtest_local server; no actual market API call.
test('real local service executes different customer programs on synthetic data', async ({ page }) => {
  await page.goto('/?local=1');
  await expect(page.locator('#execute-code')).toBeEnabled();
  await expect(page.locator('#preview-notice')).toContainText('Claude 연결 아님');
  await page.locator('#data-source').selectOption('demo');
  const hold = 'def on_bar(ctx):\n    return {"action": "hold", "reason": "보유 없이 관찰"}\n';
  await page.locator('#strategy-code').fill(hold);
  await page.locator('#validate-code').click();
  await expect(page.locator('#validation-result')).toContainText('검증 통과');
  await page.locator('#execute-code').click();
  await expect(page.locator('#report')).toBeVisible({ timeout: 30000 });
  await expect(page.locator('#trade-count')).toHaveText('0건');
  const firstRun = await page.locator('#run-meta').textContent();
  const holdEquity = await page.locator('.metric-value').last().textContent();
  const buy = 'def on_bar(ctx):\n    if ctx["index"] == 0:\n        return {"action": "buy", "quantity": 1, "reason": "첫 봉 확인 후 1주 매수"}\n    return {"action": "hold"}\n';
  await page.locator('#code-workspace > summary').click();
  await page.locator('#strategy-code').fill(buy);
  await page.locator('#execute-code').click();
  await expect(page.locator('#trade-count')).toHaveText('1건', { timeout: 30000 });
  await expect(page.locator('#run-meta')).not.toHaveText(firstRun!);
  // A one-share return can round to neutral 0.00%; compare final assets instead.
  await expect(page.locator('.metric-value').last()).not.toHaveText(holdEquity!);
  await expect(page.locator('#compare-button')).toBeHidden();
  await page.locator('.trade-button').first().click();
  await expect(page.locator('#trade-detail')).toContainText('전략 코드가 기록한 사유');
  await expect(page.locator('#trade-detail')).toContainText('첫 봉 확인 후 1주 매수');
  await expect(page.locator('#error')).toBeHidden();
  await page.screenshot({ path: test.info().outputPath('customer-code-ui.png'), fullPage: true });
});
