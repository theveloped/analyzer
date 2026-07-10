// Screenshot the accessibility mode for a given part (default aligator).
import { chromium } from 'playwright-core';

const part = process.env.PART ?? 'aligator';
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
await page.locator('.panel > select').nth(0).selectOption('access');
await page.waitForTimeout(1500);
console.log('stats:', await page.locator('.stats').textContent());
await page.screenshot({ path: process.env.OUT ?? 'access.png' });
console.log('errors:', errors.length ? errors.join('\n') : 'none');
await browser.close();
process.exit(errors.length ? 1 : 0);
