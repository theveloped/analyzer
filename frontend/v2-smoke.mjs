// v2 UI smoke test: load the v2 shell, select testpart_42, walk every pinned
// lens, every lens in the all-tools menu, the directions view and the check
// buttons. Asserts no uncaught page errors and no white screen; lenses whose
// fields are missing may report the ⚠ stats banner — that is the designed
// error path, not a failure. Run: BASE_URL=http://localhost:8080
// CHROMIUM_PATH=<chrome.exe> node v2-smoke.mjs
import { chromium } from 'playwright-core';

const base = process.env.BASE_URL ?? 'http://localhost:8080';
const executablePath = process.env.CHROMIUM_PATH;
const browser = await chromium.launch({ executablePath, args: ['--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

const pageErrors = [];
const consoleErrors = [];
page.on('pageerror', (err) => pageErrors.push(String(err)));
page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });

await page.goto(`${base}/v2.html`, { waitUntil: 'networkidle' });
await page.waitForSelector('canvas', { timeout: 20000 });

// select the fixture from the global sidebar
await page.locator('nav').getByText('testpart_42', { exact: true }).first().click();
await page.waitForTimeout(2500);

const report = [];
const statsLine = async () => {
  const rail = page.locator('.whitespace-pre-wrap').first();
  return ((await rail.textContent().catch(() => '')) || '').split('\n')[0].slice(0, 90);
};

// pinned lenses
for (const title of ['BREP faces', 'STEP colors / names', 'PMI / GD&T']) {
  await page.locator(`button[title*="${title}"]`).first().click();
  await page.waitForTimeout(900);
  report.push(`pinned ${title}: ${await statsLine()}`);
}

// directions view
await page.locator('button[title="Candidate directions"]').click();
await page.waitForTimeout(900);
report.push(`directions: ${await statsLine()}`);

// every lens in the all-tools menu (advanced on to include gated ones)
await page.locator('button[title*="More tools"], button[title*="Hide advanced"]').first().click();
await page.waitForTimeout(300);
const menuBtn = page.locator('button[title="All inspection tools"]');
await menuBtn.click();
const panel = page.locator('[id^="headlessui-popover-panel"]');
await panel.waitFor({ timeout: 5000 });
const lensLabels = (await panel.locator('button').allTextContents())
  .map((t) => t.trim()).filter(Boolean);
await page.keyboard.press('Escape');
await page.waitForTimeout(200);

for (const label of lensLabels) {
  await menuBtn.click();
  await panel.waitFor({ timeout: 5000 });
  await panel.locator('button', { hasText: label }).first().click();
  await page.waitForTimeout(800);
  report.push(`lens ${label}: ${await statsLine()}`);
}

// the check buttons (thickness/gaps + advanced ray pair)
for (const check of ['Wall thickness', 'Gap / clearance', 'Ray thickness', 'Ray gap']) {
  const btn = page.locator(`button[title^="${check}"]`).first();
  if (await btn.count()) {
    await btn.click();
    await page.waitForTimeout(700);
    report.push(`check ${check}: ${await statsLine()}`);
  }
}

if (process.env.SHOT_DIR) {
  await page.screenshot({ path: process.env.SHOT_DIR + '/v2_shell.png' });
}

console.log(report.join('\n'));
console.log('PAGE ERRORS:', pageErrors.length ? pageErrors.join('\n') : 'none');
console.log('CONSOLE ERRORS (informational):', consoleErrors.length);
await browser.close();
process.exit(pageErrors.length ? 1 : 0);
