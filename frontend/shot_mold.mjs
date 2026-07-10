// Drive the membership assignment view: screenshot, click-to-cycle a striped
// face, verify the override persists via the API.
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
await page.waitForTimeout(1500);
console.log('stats:', ((await page.locator('.stats').textContent()) ?? '').split('\n')[0]);
await page.screenshot({ path: `${process.env.OUT_DIR ?? '.'}/${part}_membership.png` });

// click a striped outer wall face a few times; watch pick + overrides
for (const [x, y] of [[420, 450], [420, 450]]) {
  await page.mouse.click(x, y);
  await page.waitForTimeout(900);
  const pick = ((await page.locator('.pick').textContent()) ?? '').split('\n').join(' | ');
  console.log('pick:', pick.slice(0, 160));
}
await page.screenshot({ path: `${process.env.OUT_DIR ?? '.'}/${part}_toggled.png` });

// override persisted server-side?
const overrides = await page.evaluate(async (p) => {
  const manifest = await (await fetch(`/api/parts/${p}/manifest`)).json();
  const result = manifest.results.find(
    (r) => r.analysis === 'mold_orientation' && r.stats.schema === 2);
  if (!result?.overrides_url) return null;
  return (await (await fetch(result.overrides_url)).json());
}, part);
console.log('server overrides:', JSON.stringify(overrides));

console.log('errors:', errors.length ? errors.join('\n') : 'none');
await browser.close();
process.exit(errors.length ? 1 : 0);
