// Field-lens spike walk: a pinned lens with no cached result must run
// itself; the rail shows the band section once the field exists; band edits
// recolor without recompute; the band saves as a plan check.
import { chromium } from 'playwright-core';

const base = process.env.BASE_URL ?? 'http://localhost:8001';
const shot = (name) => process.env.SHOT_DIR + '/' + name;
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

const railText = async () =>
  ((await page.locator('div.w-72').last().textContent()) ?? '')
    .replace(/\s+/g, ' ');

// 1. cached lens: thickness paints instantly, no run
await page.locator('button[title^="Wall thickness heatmap"]').click();
await page.waitForTimeout(2500);
const t = await railText();
console.log('thickness rail:', t.slice(0, 200));
console.log('thickness badge current:', t.includes('current'));

// 2. uncached lens: Thin span must auto-run (never computed on this part).
// Completion may outrace the badge poll — wait for the band section, which
// only renders once the fresh field exists.
await page.locator('button[title^="Thin span"]').click();
await page.waitForTimeout(1500);
console.log('auto-run observed:', /computing…/.test(await railText()));
await page.waitForFunction(
  () => /Highlight band/.test(document.body.textContent ?? '')
    && /Field spans/i.test(document.body.textContent ?? ''),
  null, { timeout: 480000 });
await page.waitForTimeout(1500);
console.log('thin span rail:', (await railText()).slice(0, 260));
await page.screenshot({ path: shot('lens_thinspan_plain.png') });

// 3. re-run must be disabled (nothing changed since the stored run)
const rerun = page.getByRole('button', { name: /Re-run analysis/ });
console.log('re-run disabled:', await rerun.isDisabled());

// 4a. "the bottom p5" in one gesture: to = 5 percentile
await page.locator('select[aria-label="band to unit"]').selectOption('pct');
await page.locator('input[aria-label="band to"]').fill('5');
await page.waitForTimeout(1500); // instant recolor, no job
console.log('bottom p5:', (await railText()).match(/highlighting [^—]+/)?.[0]);
const legendBand = await page.getByText(/in band/).count();
console.log('band legend entry:', legendBand > 0);
await page.screenshot({ path: shot('lens_band_bottom_p5.png') });

// 4b. "70–130 % of the mean"
await page.locator('select[aria-label="band from unit"]').selectOption('mean');
await page.locator('input[aria-label="band from"]').fill('70');
await page.locator('select[aria-label="band to unit"]').selectOption('mean');
await page.locator('input[aria-label="band to"]').fill('130');
await page.waitForTimeout(1500);
console.log('70-130% of mean:', (await railText()).match(/highlighting [^—]+/)?.[0]);
console.log('no job during band edits:',
  !/computing…|running…/.test(await page.locator('body').textContent() ?? ''));
await page.screenshot({ path: shot('lens_band_mean.png') });

// 5. save the band as a check (Phase-1 seeding may already own the id)
await page.getByRole('button',
  { name: /Save band as check|Update the saved check/ }).click();
await page.waitForTimeout(2000);
console.log('saved note:', (await railText()).match(/Saved as “[^”]+”[^.]*/)?.[0]);
await page.screenshot({ path: shot('lens_thinspan_saved.png') });

console.log('PAGE ERRORS:', pageErrors.length ? pageErrors.join('\n') : 'none');
await browser.close();
process.exit(pageErrors.length ? 1 : 0);
