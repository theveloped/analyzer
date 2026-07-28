import { chromium } from 'playwright-core';
const OUT = '/tmp/claude-0/-home-user-analyzer/c5d338a8-363e-5023-9c01-27b0b4610637/scratchpad';
const browser = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium',
  args: ['--enable-unsafe-swiftshader', '--use-gl=swiftshader'],
});
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
page.setDefaultTimeout(300000);
page.on('pageerror', (e) => console.log('PAGEERROR', String(e).slice(0, 300)));
await page.goto('http://localhost:8080/v2.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(18000);
await page.locator('button[title*="More tools"], button[title*="Hide advanced"]').first().click();
await page.waitForTimeout(800);
const menuBtn = page.locator('button[title="All inspection tools"]');
await menuBtn.click();
const panel = page.locator('[id^="headlessui-popover-panel"]');
await panel.waitFor({ timeout: 30000 });
await panel.locator('button', { hasText: 'Turning roles' }).first().click();
await page.waitForTimeout(240000);
const legend = await page.locator('text=TURNING ROLES').locator('xpath=..').innerText().catch(() => '');
console.log('LEGEND:', legend.slice(0, 900));
await page.screenshot({ path: `${OUT}/turning.png`, animations: 'disabled', timeout: 300000 });
console.log('shot ok');
await browser.close();
