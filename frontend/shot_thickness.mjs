// Screenshot the thickness and gaps heatmap modes for a part (default aligator).
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

await page.click('.tabs button:nth-child(2)'); // injection molding tab
await page.waitForTimeout(500);

for (const mode of ['thickness', 'gaps']) {
  await page.locator('.panel > select').nth(0).selectOption(mode);
  await page.waitForTimeout(1500);
  console.log(`${mode} stats:`, await page.locator('.stats').textContent());
  await page.screenshot({ path: `${process.env.OUT_DIR ?? '.'}/${part}_${mode}.png` });
}

// click-to-inspect: both maps at one face
await page.mouse.click(900, 450);
await page.waitForTimeout(800);
console.log('pick:', ((await page.locator('.pick').textContent()) ?? '').split('\n').join(' | '));

console.log('errors:', errors.length ? errors.join('\n') : 'none');
await browser.close();
process.exit(errors.length ? 1 : 0);
