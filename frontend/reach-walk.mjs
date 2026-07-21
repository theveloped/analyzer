// Phase-2 exploration walk: seed the CNC exploration template, trim the
// study to 2 directions × 2 tools (walk speed), run it, check per-op and
// route slices, flip OP20's direction through the impact modal (expecting
// zero recompute), and verify the re-slice.
import { chromium } from 'playwright-core';

const base = process.env.BASE_URL ?? 'http://localhost:8001';
const shot = (name) => process.env.SHOT_DIR + '/' + name;
const api = async (path, init) => {
  const res = await fetch(`${base}/api/parts/testpart_42${path}`, init);
  if (!res.ok) throw new Error(`${path}: ${res.status} ${await res.text()}`);
  return res.json();
};

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH, args: ['--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const pageErrors = [];
page.on('pageerror', (err) => pageErrors.push(String(err)));

await page.goto(`${base}/v2.html`, { waitUntil: 'networkidle' });
await page.waitForSelector('canvas', { timeout: 20000 });
await page.locator('nav').getByText('testpart_42', { exact: true }).first().click();
await page.waitForTimeout(2500);

// 1. seed the exploration template through the UI (skip when already seeded)
const seedBtn = page.getByRole('button', { name: /Add CNC exploration/ });
if (await seedBtn.count()) {
  await seedBtn.click();
  await page.waitForTimeout(1500);
}
console.log('ops present:', await page.getByText('OP10').count(),
  await page.getByText('OP20').count());

// 2. trim the study for walk speed: 2 directions × 2 tools (an API-side plan
// edit, same as an engineer editing the study scope)
{
  const section = await api('/plan');
  const plan = section.plan;
  const params = {
    direction_indices: [4, 5],
    tools: [
      { diameter: 8.0, corner_radius: 0.0, stickout: 40.0, holder_radius: 4.0 },
      { diameter: 4.0, corner_radius: 0.0, stickout: 20.0, holder_radius: 2.0 },
    ],
  };
  for (const c of plan.checks) {
    if (c.analysis === 'cnc/reach_study') c.params = params;
  }
  await api('/plan', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan, revision: plan.revision }),
  });
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
}

// 3. open the study check; run it when it has no current result yet
await page.getByRole('button', { name: /Reach study/ }).first().click();
await page.waitForTimeout(600);
const runBtn = page.getByRole('button', { name: /^Run$/ });
if (await runBtn.count()) {
  await runBtn.click();
  await page.waitForFunction(() =>
    /running…|queued…/.test(document.body.textContent ?? ''),
  null, { timeout: 30000 });
  console.log('study running…');
  await page.waitForFunction(() =>
    !/running…|queued…/.test(document.body.textContent ?? ''),
  null, { timeout: 480000 });
  await page.waitForTimeout(3000);
  console.log('study done');
} else {
  console.log('study already current (cached)');
}
await page.screenshot({ path: shot('reach_study.png') });

// 4. per-op slice: OP10 check paints + evaluates without any new run
await page.getByRole('button', { name: /Reach — OP10/ }).click();
await page.waitForTimeout(4000);
const railText = async () =>
  ((await page.locator('div.w-72').last().textContent()) ?? '')
    .replace(/\s+/g, ' ').slice(0, 240);
console.log('OP10 rail:', await railText());
await page.screenshot({ path: shot('reach_op10.png') });

// 5. route aggregate
await page.getByRole('button', { name: /Route reach/ }).click();
await page.waitForTimeout(4000);
console.log('route rail:', await railText());
await page.screenshot({ path: shot('reach_route.png') });

// 6. flip OP20's direction → impact modal must say nothing recomputes
const selects = page.locator('div.w-64 select');
const op20Select = selects.nth(1);
const currentDir = await op20Select.inputValue();
const nextDir = currentDir === '0' ? '1' : '0';
await op20Select.selectOption(nextDir);
// the role=dialog wrapper is zero-size (headlessui portal) — wait on content;
// then wait for the impact rows to replace "Computing impact…"
await page.getByRole('button', { name: 'Apply', exact: true })
  .waitFor({ timeout: 10000 });
await page.waitForFunction(() => {
  const d = document.querySelector('[role=dialog]')?.textContent ?? '';
  return /unchanged|revalidates|recomputes|⚠/.test(d);
}, null, { timeout: 15000 });
const dialogText = ((await page.locator('[role=dialog]').textContent()) ?? '')
  .replace(/\s+/g, ' ');
console.log('impact modal:', dialogText.slice(0, 300));
const zeroRecompute = !dialogText.includes('recomputes');
console.log('zero recompute:', zeroRecompute);
await page.screenshot({ path: shot('reach_impact.png') });
await page.getByRole('button', { name: 'Apply', exact: true }).click();
await page.waitForTimeout(2500);

// 7. OP20 now slices direction 0 — no job should be running
const bodyAfter = (await page.locator('body').textContent()) ?? '';
console.log('job running after apply:', /running…|queued…/.test(bodyAfter));
await page.getByRole('button', { name: /Reach — OP20/ }).click();
await page.waitForTimeout(4000);
console.log('OP20 rail after flip:', await railText());
await page.screenshot({ path: shot('reach_op20_flipped.png') });

console.log('PAGE ERRORS:', pageErrors.length ? pageErrors.join('\n') : 'none');
await browser.close();
process.exit(pageErrors.length || !zeroRecompute ? 1 : 0);
