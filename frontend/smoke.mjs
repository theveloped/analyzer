// UI smoke test: load the app on testpart_42 (has zcache fields), walk all
// CNC modes, switch to injection molding coverage, capture console errors.
import { chromium } from 'playwright-core';

const executablePath = process.env.CHROMIUM_PATH;
const browser = await chromium.launch({ executablePath, args: ['--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

const errors = [];
page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
page.on('pageerror', (err) => errors.push(String(err)));

await page.goto('http://localhost:8000/', { waitUntil: 'networkidle' });
await page.waitForSelector('.canvas-host canvas', { timeout: 20000 });

// select the part with precomputed CNC fields
await page.selectOption('.panel .row select', 'testpart_42');
await page.waitForFunction(
  () => document.querySelector('.legend')?.children.length > 0, null, { timeout: 30000 });

const modeSelect = page.locator('.panel > select').nth(0);
const report = [];
for (const mode of ['unified', 'access', 'class', 'gap', 'stickout', 'diff', 'highlights']) {
  await modeSelect.selectOption(mode);
  await page.waitForTimeout(700);
  const stats = await page.locator('.stats').textContent().catch(() => '');
  const legendCount = await page.locator('.legend div').count();
  report.push(`${mode}: legend=${legendCount} stats=${(stats || '').split('\n')[0].slice(0, 90)}`);
  if (mode === 'unified') {
    await page.screenshot({ path: process.env.SHOT_DIR + '/cnc_unified.png' });
  }
}

// stickout mode needs a holder — enter one and re-check
await modeSelect.selectOption('stickout');
await page.fill('.panel input[type=text]', '8:0');
await page.waitForTimeout(1000);
report.push('stickout+holder: ' + (await page.locator('.stats').textContent() || '').split('\n')[0]);

// injection molding tab (large_part has the parting result)
await page.selectOption('.panel .row select', 'large_part');
await page.waitForFunction(
  () => (document.querySelector('.stats')?.textContent ?? '').length > 0, null, { timeout: 60000 });
await page.click('.tabs button:nth-child(2)');
await page.waitForFunction(
  () => document.querySelector('.legend')?.children.length > 0, null, { timeout: 30000 });
await page.waitForTimeout(1500);
report.push('IM coverage: ' + ((await page.locator('.stats').textContent().catch(() => '')) || '').split('\n')[0]);
await page.screenshot({ path: process.env.SHOT_DIR + '/im_coverage.png' });

// click to inspect
await page.mouse.click(900, 450);
await page.waitForTimeout(500);
report.push('pick: ' + ((await page.locator('.pick').textContent()) || '').split('\n').join(' | ').slice(0, 120));

console.log(report.join('\n'));
console.log('CONSOLE ERRORS:', errors.length ? errors.join('\n') : 'none');
await browser.close();
process.exit(errors.length ? 1 : 0);
