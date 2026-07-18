// UI smoke for the v2 shell (served statically, no backend). Confirms the
// React shell mounts, shadcn/Tailwind render, the viewer canvas attaches, the
// sidebar collapses, and analyses switch — capturing genuine runtime errors
// (expected /api network failures are filtered out).
import { chromium } from 'playwright-core';

const executablePath = process.env.CHROMIUM_PATH
  || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const base = process.env.BASE_URL || 'http://localhost:4180';
const browser = await chromium.launch({ executablePath, args: ['--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

const errors = [];
page.on('console', (msg) => {
  if (msg.type() !== 'error') return;
  const t = msg.text();
  if (/Failed to load resource|\/api\/|net::ERR|status of 404|status of 502/.test(t)) return;
  errors.push(t);
});
page.on('pageerror', (err) => errors.push(String(err)));

await page.goto(`${base}/v2.html`, { waitUntil: 'load' });
const report = [];

// shell mounts
await page.waitForSelector('text=DFM Studio', { timeout: 15000 });
report.push('brand rendered');

// viewer canvas attached
await page.waitForSelector('canvas', { timeout: 15000 });
report.push('viewer canvas attached');

// floating analysis toolbar + settings rail present
report.push('toolbar buttons=' + await page.getByLabel('Wall thickness').count());
report.push('run button=' + await page.getByRole('button', { name: /Run check|Running|Re-run/ }).count());

// switch to the Gap analysis via the toolbar
await page.getByLabel('Gap / clearance').first().click();
await page.waitForTimeout(300);
report.push('after gap click, settings heading=' +
  (await page.locator('h2').first().textContent().catch(() => '')));

// reveal advanced analyses + settings
await page.getByLabel('Toggle advanced analyses').click();
await page.waitForTimeout(200);
report.push('advanced analyses visible=' + await page.getByLabel('Ray thickness').count());

// collapse the left sidebar
await page.getByLabel('Toggle sidebar').click();
await page.waitForTimeout(300);
report.push('sidebar toggled');

await page.screenshot({ path: (process.env.SHOT_DIR || '/tmp') + '/v2_workspace.png' });

console.log(report.join('\n'));
console.log('CONSOLE ERRORS:', errors.length ? errors.join('\n') : 'none');
await browser.close();
process.exit(errors.length ? 1 : 0);
