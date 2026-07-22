// v2 UI smoke test: load the v2 shell, select testpart_42, walk every pinned
// lens, every lens in the all-tools menu, the directions view and the check
// buttons — then the viewport toolbar: every render style, projection
// switch, section plane sweep and a two-point measurement, with canvas-pixel
// checks (part visible, lens colours survive every base style, measurement
// annotations survive lens repaints) and bottom-overlay overlap checks at
// desktop and narrow widths. Asserts no uncaught page errors and no white
// screen; lenses whose fields are missing may report the ⚠ stats banner —
// that is the designed error path, not a failure.
// Run: BASE_URL=http://localhost:8080 CHROMIUM_PATH=<chrome.exe> node v2-smoke.mjs
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

// ------------------------------------------------------------------------
// Backlog 19: viewport toolbar, render layers, section plane, measurement.
// Pixels come from the in-app capture (window.__viewerCapture) — the WebGL
// canvas has no preserveDrawingBuffer, so page.screenshot can't sample it.
const failures = [];
const check = (cond, msg) => {
  report.push(`${cond ? 'ok  ' : 'FAIL'} ${msg}`);
  if (!cond) failures.push(msg);
};

const capturePixels = () => page.evaluate(async () => {
  const cap = window.__viewerCapture?.();
  if (!cap) return null;
  const img = new Image();
  await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = cap.image; });
  const c = document.createElement('canvas');
  c.width = img.width;
  c.height = img.height;
  const g = c.getContext('2d');
  g.drawImage(img, 0, 0);
  const d = g.getImageData(0, 0, c.width, c.height).data;
  const bg = [d[0], d[1], d[2]]; // top-left corner = clear colour
  let part = 0;
  let chroma = 0;
  let marker = 0;
  for (let i = 0; i < d.length; i += 16) { // every 4th pixel
    const r = d[i];
    const gg = d[i + 1];
    const b = d[i + 2];
    if (Math.abs(r - bg[0]) + Math.abs(gg - bg[1]) + Math.abs(b - bg[2]) > 30) part++;
    // Phong lighting compresses saturation — lit segment colours spread
    // ~30-45 while the neutral base/background stay under ~10
    if (Math.max(r, gg, b) - Math.min(r, gg, b) > 25) chroma++;
    if (b > 150 && b - r > 60 && b - gg > 40) marker++; // measure-marker blue
  }
  return { part, chroma, marker, projection: cap.camera.projection };
});

// a colour-rich lens so "lens colours survive every base style" is testable
await page.locator('button[title*="BREP faces"]').first().click();
await page.waitForTimeout(900);

// every render style keeps the part visible and the lens colours saturated
for (const style of ['Mesh', 'X-ray', 'Solid']) {
  await page.locator(`button[title^="${style} —"]`).click();
  await page.waitForTimeout(500);
  const px = await capturePixels();
  check(px && px.part > 200, `style ${style}: part visible (${px?.part} px)`);
  check(px && px.chroma > 50, `style ${style}: lens colours survive (${px?.chroma} px)`);
}

// BREP edges default ON — toggling off and back on keeps the part visible
await page.locator('button[title^="Show BREP boundary edges"]').click();
await page.waitForTimeout(400);
await page.locator('button[title^="Show BREP boundary edges"]').click();
await page.waitForTimeout(600);
const edgesPx = await capturePixels();
check(edgesPx && edgesPx.part > 200, `BREP edges on: part visible (${edgesPx?.part} px)`);

// inline overlay sliders: lens colours to 0 → grey base (chroma collapses);
// icon click toggles straight back to 100%
await page.evaluate(() => {
  const el = document.querySelector('input[title^="Lens colours opacity"]');
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  setter.call(el, '0');
  el.dispatchEvent(new Event('input', { bubbles: true }));
});
await page.waitForTimeout(400);
const lensOffPx = await capturePixels();
check(edgesPx && lensOffPx && lensOffPx.chroma < edgesPx.chroma * 0.2,
  `lens slider to 0 hides the colours (${edgesPx?.chroma} → ${lensOffPx?.chroma} px)`);
await page.locator('button[title^="Lens colours"]').click(); // toggle back to 100%
await page.waitForTimeout(400);
const lensOnPx = await capturePixels();
check(lensOnPx && edgesPx && lensOnPx.chroma > edgesPx.chroma * 0.8,
  `lens icon toggles back to 100% (${lensOnPx?.chroma} px)`);

// the Voxel style computes prep/voxels on first use — until it lands the
// solid look stays up; assert the fallback and no errors, then return
await page.locator('button[title^="Voxel —"]').click();
await page.waitForTimeout(1200);
const voxelPx = await capturePixels();
check(voxelPx && voxelPx.part > 200, `voxel style (fallback while computing): part visible (${voxelPx?.part} px)`);
await page.locator('button[title^="Solid —"]').click();
await page.waitForTimeout(300);

// projection round-trip preserves the view
const perspPx = await capturePixels();
await page.locator('button[title="Switch to orthographic projection"]').click();
await page.waitForTimeout(400);
const orthoPx = await capturePixels();
check(orthoPx?.projection === 'orthographic', 'projection switch reaches the capture pose');
check(orthoPx && orthoPx.part > 200, `orthographic: part visible (${orthoPx?.part} px)`);
check(orthoPx && perspPx && Math.abs(orthoPx.part - perspPx.part) < perspPx.part * 0.5,
  `apparent size preserved across the switch (${perspPx?.part} → ${orthoPx?.part} px)`);
await page.locator('button[title="Switch to perspective projection"]').click();
await page.waitForTimeout(300);

// section: the Slice button opens the section RAIL (right side, like measure)
const fullPx = await capturePixels();
await page.locator('button[title^="Section plane"]').click();
const sectionRail = page.locator('h2', { hasText: /^Section$/ })
  .locator('xpath=ancestor::div[contains(@class,"w-72")]');
await page.locator('h2', { hasText: /^Section$/ }).waitFor({ timeout: 5000 });
check(true, 'section rail opens');
const setOffset = (fraction) => page.evaluate((t) => {
  const el = document.querySelector('input[title="Section offset"]');
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  const lo = parseFloat(el.min);
  const hi = parseFloat(el.max);
  setter.call(el, String(lo + (hi - lo) * t));
  el.dispatchEvent(new Event('input', { bubbles: true }));
}, fraction);
await sectionRail.locator('button', { hasText: /^x$/i }).first().click();
await page.waitForTimeout(600);
// mid offset: half the part gone, the stencil cap fills the cut solid
const capPx = await capturePixels();
check(fullPx && capPx && capPx.part > 100 && capPx.part < fullPx.part,
  `section mid-cut with cap: part visible (${capPx?.part} px)`);
await setOffset(0.02); // just above the low corner: (nearly) everything cut
await page.waitForTimeout(400);
const cutPx = await capturePixels();
check(fullPx && cutPx && capPx && cutPx.part < capPx.part && cutPx.part < fullPx.part * 0.6,
  `section sweep cuts the part (${fullPx?.part} → ${capPx?.part} → ${cutPx?.part} px)`);
await sectionRail.locator('button', { hasText: 'Reset' }).click();
await page.waitForTimeout(300);
const resetPx = await capturePixels();
check(resetPx && fullPx && resetPx.part > fullPx.part * 0.8,
  `section reset restores the part (${resetPx?.part} px)`);

// snap: "Pick target" arms a one-shot pick; clicking a face snaps the plane
// (needs brep_meta_url from a current server; falls back to a vertex snap)
await sectionRail.locator('button', { hasText: 'Pick target' }).click();
await page.waitForTimeout(200);
const cv0 = await page.locator('canvas').boundingBox();
await page.mouse.click(cv0.x + cv0.width / 2, cv0.y + cv0.height / 2);
await page.waitForTimeout(800);
const snapOn = await page.locator('button[title^="Section plane"]')
  .evaluate((el) => el.getAttribute('aria-pressed') === 'true');
check(snapOn, 'pick-target snap enables the section on the picked face');
// close the rail; viewport reset restores defaults for the rest of the walk
await sectionRail.locator('button[title^="Close"]').click();
await page.locator('button[title^="Reset viewport"]').click();
await page.waitForTimeout(400);

// two-point measurement: rail readout + annotations that survive a repaint
const canvasBox = await page.locator('canvas').boundingBox();
const cx = canvasBox.x + canvasBox.width / 2;
const cy = canvasBox.y + canvasBox.height / 2;
await page.locator('button[title="Measure two points"]').click();
await page.mouse.click(cx - 60, cy - 20);
await page.waitForTimeout(300);
await page.mouse.click(cx + 60, cy + 20);
await page.waitForTimeout(500);
const railText = await page.locator('h2', { hasText: 'Measure' })
  .locator('xpath=ancestor::div[contains(@class,"w-72")]').textContent();
check(railText?.includes('picked points'), 'measure rail reports the picked-point distance');
check(/dX/.test(railText ?? ''), 'measure rail reports signed component deltas');
// component-frame toggle: normal-A decomposition swaps the readout rows
await page.locator('button', { hasText: /^Normal A$/ }).first().click();
await page.waitForTimeout(300);
const frameText = await page.locator('h2', { hasText: 'Measure' })
  .locator('xpath=ancestor::div[contains(@class,"w-72")]').textContent();
check(/along A's normal/.test(frameText ?? ''),
  'normal-A frame reports the along-normal split');
await page.locator('button', { hasText: /^XYZ$/ }).first().click();
await page.waitForTimeout(200);
const measuredPx = await capturePixels();
check(measuredPx && measuredPx.marker > 0,
  `measurement markers drawn (${measuredPx?.marker} px)`);
// lens repaint must not clear the annotation layer
await page.locator('button[title*="STEP colors / names"]').first().click();
await page.waitForTimeout(900);
const afterRepaintPx = await capturePixels();
check(afterRepaintPx && afterRepaintPx.marker > 0,
  `annotations survive a lens repaint (${afterRepaintPx?.marker} px)`);
await page.keyboard.press('Escape'); // exit measure

// bottom overlays may never overlap: legend (left), viewport toolbar
// (centre), axis gizmo (the canvas' bottom-right 128×128), at desktop AND
// narrow widths
const intersects = (a, b) => a && b
  && a.x < b.x + b.width && b.x < a.x + a.width
  && a.y < b.y + b.height && b.y < a.y + a.height;
const overlapCheck = async (tag) => {
  const toolbar = await page.locator('button[title="Fit part in view"]')
    .locator('xpath=..').boundingBox();
  const legend = await page.locator('div[class*="bottom-3"][class*="left-3"]')
    .boundingBox().catch(() => null);
  const cv = await page.locator('canvas').boundingBox();
  const gizmo = cv && {
    x: cv.x + cv.width - 128, y: cv.y + cv.height - 128, width: 128, height: 128,
  };
  check(!intersects(toolbar, legend), `${tag}: toolbar clear of the legend`);
  check(!intersects(toolbar, gizmo), `${tag}: toolbar clear of the axis gizmo`);
  check(!intersects(legend, gizmo), `${tag}: legend clear of the axis gizmo`);
};
await overlapCheck('1600×1000');
if (process.env.SHOT_DIR) {
  await page.screenshot({ path: process.env.SHOT_DIR + '/v2_shell.png' });
}
await page.setViewportSize({ width: 900, height: 700 });
await page.waitForTimeout(600);
await overlapCheck('900×700');
const narrowPx = await capturePixels();
check(narrowPx && narrowPx.part > 100, `narrow viewport: part visible (${narrowPx?.part} px)`);
if (process.env.SHOT_DIR) {
  await page.screenshot({ path: process.env.SHOT_DIR + '/v2_shell_narrow.png' });
}

console.log(report.join('\n'));
console.log('PAGE ERRORS:', pageErrors.length ? pageErrors.join('\n') : 'none');
console.log('CONSOLE ERRORS (informational):', consoleErrors.length);
console.log('VIEWPORT CHECKS:', failures.length ? `${failures.length} FAILED` : 'all passed');
await browser.close();
process.exit(pageErrors.length || failures.length ? 1 : 0);
