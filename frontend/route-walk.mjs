// Phase-4 walk: upload an L-bracket with holes, instantiate the mixed
// laser → CNC → press-brake route, run every step's check, and publish
// the mixed-route report. FIXTURE points at the STEP file.
import { readFileSync } from 'node:fs';
import { chromium } from 'playwright-core';

const base = process.env.BASE_URL ?? 'http://localhost:8001';
const shot = (n) => process.env.SHOT_DIR + '/' + n;

// 1. upload the fixture through the API (dedup-idempotent)
const form = new FormData();
form.append('file', new Blob([readFileSync(process.env.FIXTURE)]),
  'bracket_holes.step');
const up = await fetch(`${base}/api/parts`, { method: 'POST', body: form });
if (!up.ok) throw new Error(`upload: ${up.status} ${await up.text()}`);
const part = await up.json();
console.log('part:', part.id, part.name);

const jobsIdle = async () => {
  const jobs = await (await fetch(
    `${base}/api/jobs?part_id=${part.id}`)).json();
  return !jobs.some((j) => j.status === 'queued' || j.status === 'running');
};
const waitIdle = async (ms) => {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (await jobsIdle()) return;
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error('jobs never went idle');
};
await waitIdle(300000); // the upload bundle (coarse + aag + attrs)

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH, args: ['--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', (e) => errs.push(String(e)));
await page.goto(`${base}/v2.html`, { waitUntil: 'networkidle' });
await page.waitForSelector('canvas', { timeout: 20000 });
await page.locator('nav').getByText('bracket_holes', { exact: true }).first().click();
await page.waitForTimeout(3000);

// 2. instantiate the route
await page.getByRole('button', { name: /Add route: Laser/ }).click();
await page.waitForFunction(
  () => /Laser blank/.test(document.body.textContent ?? ''),
  null, { timeout: 20000 });
console.log('route instantiated:', await page.getByText(/Plan · rev/).textContent());
await page.screenshot({ path: shot('route_steps.png') });

// 3. run every step's check, one at a time (single job worker)
const CHECKS = ['Sheet detection', 'Flat pattern', 'Feature recognition',
  'Reach — CNC features', 'Bend plan'];
for (const label of CHECKS) {
  await page.locator('div.w-64').getByRole('button', { name: label }).click();
  await page.waitForTimeout(800);
  const run = page.getByRole('button', { name: /^(Run|Re-run)$/ });
  if (await run.count()) {
    await run.first().click();
    await page.waitForFunction(() =>
      /running…|queued…/.test(document.body.textContent ?? ''),
    null, { timeout: 30000 }).catch(() => {});
    await page.waitForFunction(() =>
      !/running…|queued…/.test(document.body.textContent ?? ''),
    null, { timeout: 600000 });
    await page.waitForTimeout(2500);
  }
  const rail = ((await page.locator('div.w-72').last().textContent()) ?? '')
    .replace(/\s+/g, ' ');
  console.log(`${label}:`, rail.slice(0, 150));
}
await page.screenshot({ path: shot('route_checks.png') });

// 4. publish the mixed-route report and open it
await page.getByRole('button', { name: 'Publish report' }).click();
await page.waitForFunction(
  () => window.location.hash.startsWith('#report='), null, { timeout: 300000 });
await page.waitForFunction(
  () => /plan revision/.test(document.body.textContent ?? ''),
  null, { timeout: 20000 });
await page.waitForTimeout(1500);
const body = (await page.locator('body').textContent()) ?? '';
console.log('report cards:',
  ['Sheet detection', 'Flat pattern', 'Reach', 'Bend plan']
    .map((l) => `${l}=${body.includes(l)}`).join(' '));
console.log('screenshots:', await page.locator('img[alt$="view"]').count());
await page.screenshot({ path: shot('route_report.png'), fullPage: false });

console.log('PAGE ERRORS:', errs.length ? errs.join('\n') : 'none');
await browser.close();
process.exit(errs.length ? 1 : 0);
