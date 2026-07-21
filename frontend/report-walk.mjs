// Phase-3 walk: materialize the ray fields (auto-run), publish the plan's
// visible checks as a report bundle, open the read-only view, verify the
// content, and confirm the sidebar lists the bundle.
import { chromium } from 'playwright-core';

const base = process.env.BASE_URL ?? 'http://localhost:8001';
const shot = (n) => process.env.SHOT_DIR + '/' + n;
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH, args: ['--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', (e) => errs.push(String(e)));

await page.goto(`${base}/v2.html`, { waitUntil: 'networkidle' });
await page.waitForSelector('canvas', { timeout: 20000 });
await page.locator('nav').getByText('testpart_42', { exact: true }).first().click();
await page.waitForTimeout(2500);

// 1. materialize the two ray fields via lens auto-run (report determinism)
for (const title of ['Ray wall thickness', 'Ray wall gap']) {
  await page.locator(`button[title^="${title}"]`).click();
  await page.waitForFunction(
    () => /Highlight band/.test(document.body.textContent ?? '')
      && /Field spans/i.test(document.body.textContent ?? ''),
    null, { timeout: 480000 });
  console.log(`${title}: field ready`);
}

// 2. publish — walks every visible check, captures, posts the bundle
await page.getByRole('button', { name: 'Publish report' }).click();
await page.waitForFunction(
  () => window.location.hash.startsWith('#report='), null, { timeout: 240000 });
console.log('report route:', await page.evaluate(() => window.location.hash));
await page.waitForFunction(
  () => /plan revision/.test(document.body.textContent ?? ''),
  null, { timeout: 20000 });
await page.waitForTimeout(1500);

// 3. verify the read-only content
const body = (await page.locator('body').textContent()) ?? '';
console.log('has title:', /DFM report/.test(body));
console.log('check cards:',
  (body.match(/Wall thickness|Reach — OP10|Route reach/g) ?? []).length >= 3);
console.log('disposition shown:', /accepted/.test(body));
const shots = await page.locator('img[alt$="view"]').count();
console.log('screenshots rendered:', shots);
await page.screenshot({ path: shot('report_view.png'), fullPage: false });

// 4. back to the workspace; the sidebar lists the bundle
await page.getByRole('button', { name: /Back to the workspace/ }).click();
await page.waitForTimeout(1500);
console.log('sidebar lists report:',
  await page.locator('nav').getByText(/DFM report · rev/).count());

console.log('PAGE ERRORS:', errs.length ? errs.join('\n') : 'none');
await browser.close();
process.exit(errs.length ? 1 : 0);
