// Screenshot the mold assignment view (band/resolved/brep) for a part.
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
await page.locator('.panel > select').nth(0).selectOption('assignment');
await page.waitForTimeout(1500);

for (const display of ['band', 'resolved', 'brep']) {
  // the display select is the third select in the assignment controls
  const selects = page.locator('.panel select');
  await selects.nth(4).selectOption(display).catch(async () => {
    // panel layout: part, mode, result, option, display
    await selects.nth(3).selectOption(display);
  });
  await page.waitForTimeout(1200);
  console.log(`${display}:`, ((await page.locator('.stats').textContent()) ?? '').split('\n')[0]);
  await page.screenshot({ path: `${process.env.OUT_DIR ?? '.'}/${part}_assign_${display}.png` });
}

console.log('errors:', errors.length ? errors.join('\n') : 'none');
await browser.close();
process.exit(errors.length ? 1 : 0);
