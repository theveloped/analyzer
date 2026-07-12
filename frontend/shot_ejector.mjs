// Screenshot the ejector-pin mode for a part: sticking heatmap, then the
// deflection view after placing two pins by clicking. Assumes the API
// server runs on :8000 and the part has a cached ejection_sticking result.
import { chromium } from 'playwright-core';

const part = process.env.PART ?? '21007-010-rev1-240103_assy-wireless_top';
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

await page.locator('.panel > select').nth(0).selectOption('ejector');
await page.waitForTimeout(2500);
console.log('sticking stats:', await page.locator('.stats').textContent());
await page.screenshot({ path: `${process.env.OUT_DIR ?? '.'}/${part}_ejector.png` });

// place two pins by clicking the part
await page.mouse.click(700, 350);
await page.waitForTimeout(2500);
await page.mouse.click(700, 650);
await page.waitForTimeout(2500);
console.log('pins stats:', await page.locator('.stats').textContent());
console.log('pin list entries:', await page.locator('.proposal-list button').count());
await page.screenshot({ path: `${process.env.OUT_DIR ?? '.'}/${part}_ejector_pins.png` });

// remove one pin by clicking it again
await page.mouse.click(700, 350);
await page.waitForTimeout(2000);
console.log('after remove:', await page.locator('.proposal-list button').count());

console.log('errors:', errors.length ? errors.join('\n') : 'none');
await browser.close();
process.exit(errors.length ? 1 : 0);
