// Screenshot the sprue-proposal mode for a part (markers, then a selected
// proposal's fill + weld overlay). Assumes the API server runs on :8000 and
// the part has a cached sprue_proposals result.
import { chromium } from 'playwright-core';

const part = process.env.PART ?? 'testpart_42';
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const errors = [];
page.on('pageerror', (err) => errors.push(String(err)));

await page.goto('http://localhost:8000/', { waitUntil: 'networkidle' });
await page.waitForSelector('.canvas-host canvas', { timeout: 20000 });
await page.selectOption('.panel .row select', part);
await page.waitForFunction(
  () => (document.querySelector('.stats')?.textContent ?? '').length > 0, null, { timeout: 60000 });

await page.click('.tabs button:nth-child(2)'); // injection molding tab
await page.waitForTimeout(500);

await page.locator('.panel > select').nth(0).selectOption('sprue');
await page.waitForTimeout(2500);
console.log('sprue stats:', await page.locator('.stats').textContent());
await page.screenshot({ path: `${process.env.OUT_DIR ?? '.'}/${part}_sprue.png` });

// select the top proposal from the list
await page.locator('.proposal-list button').first().click();
await page.waitForTimeout(2500);
console.log('selected stats:', await page.locator('.stats').textContent());
await page.screenshot({ path: `${process.env.OUT_DIR ?? '.'}/${part}_sprue_selected.png` });

console.log('errors:', errors.length ? errors.join('\n') : 'none');
await browser.close();
process.exit(errors.length ? 1 : 0);
