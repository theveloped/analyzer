// Targeted smoke test for the voxel flow views: walks flowFill / cooling /
// voxelField on a part with flow_voxels + flow_fill results, places a gate
// by clicking the part and runs a Compute-fill job end-to-end.
// Usage: CHROMIUM_PATH=... BASE_URL=http://localhost:8080 node smoke_flow.mjs
import { chromium } from 'playwright-core';

const base = process.env.BASE_URL ?? 'http://localhost:8080';
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

const errors = [];
page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
page.on('pageerror', (err) => errors.push(String(err)));

await page.goto(base + '/', { waitUntil: 'networkidle' });
await page.waitForSelector('.canvas-host canvas', { timeout: 20000 });
await page.selectOption('.panel .row select', 'testpart_42');
await page.waitForTimeout(1500);

// injection molding tab
await page.click('.tabs button:nth-child(2)');
await page.waitForTimeout(1000);

const modeSelect = page.locator('.panel > select').nth(0);
const report = [];
const stats = async () =>
  ((await page.locator('.stats').textContent().catch(() => '')) || '')
    .split('\n')[0].slice(0, 110);

for (const mode of ['voxelField', 'cooling', 'flowFill']) {
  await modeSelect.selectOption(mode);
  await page.waitForTimeout(1500);
  const legendCount = await page.locator('.legend div').count();
  report.push(`${mode}: legend=${legendCount} stats=${await stats()}`);
}

// voxelField: switch the scalar to arrival (needs the stored fill result)
await modeSelect.selectOption('voxelField');
await page.waitForTimeout(800);
await page.locator('select:has(option[value="arrival"])').selectOption('arrival');
await page.waitForTimeout(1200);
report.push(`voxelField/arrival: stats=${await stats()}`);

// flowFill: click the part to place a gate, then run the job
await modeSelect.selectOption('flowFill');
await page.waitForTimeout(800);
await page.mouse.click(700, 450);
await page.waitForTimeout(800);
const computeButton = page.locator('button', { hasText: 'Compute fill' });
const disabled = await computeButton.isDisabled().catch(() => true);
report.push(`gate click: compute-button ${disabled ? 'DISABLED' : 'enabled'}`);
if (!disabled) {
  await computeButton.click();
  await page.waitForFunction(
    () => !/computing/.test(
      document.querySelector('.controls, .panel')?.textContent ?? ''),
    null, { timeout: 120000 }).catch(() => report.push('job wait timed out'));
  await page.waitForTimeout(2000);
  report.push(`after compute: stats=${await stats()}`);
}
if (process.env.SHOT_DIR) {
  await page.screenshot({ path: process.env.SHOT_DIR + '/flow_fill.png' });
}

console.log(report.join('\n'));
console.log('CONSOLE ERRORS:', errors.length ? errors.join('\n') : 'none');
await browser.close();
process.exit(errors.length ? 1 : 0);
