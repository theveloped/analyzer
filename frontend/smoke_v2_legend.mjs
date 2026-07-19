// Visual check for the dataviz color/legend pass: seeds a realistic thickness
// legend + stats into the store (dev-only __store seam) so we can eyeball the
// sequential ramp legend and the status system without a backend.
import { chromium } from 'playwright-core';

const executablePath = process.env.CHROMIUM_PATH
  || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const base = process.env.BASE_URL || 'http://localhost:4190';
const browser = await chromium.launch({ executablePath, args: ['--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.on('pageerror', (e) => console.log('PAGEERROR', String(e)));

await page.goto(`${base}/v2.html`, { waitUntil: 'load' });
await page.waitForSelector('text=DFM Studio', { timeout: 15000 });

// seed a thickness result (dot in the pipeline goes "computed"/good) + a legend
// mirroring what the thickness heatmap mode emits, so the ramp legend paints.
await page.evaluate(() => {
  const store = window.__store;
  const RAMP1 = [0.48, 0.02, 0.01];
  store.getState().set({
    meshReady: true,
    partId: 'demo',
    manifest: {
      part: { id: 'demo', name: 'Housing', source: null, status: 'meshed', counts: { verts: 1, faces: 1 }, has_directions: false, created: null },
      mesh: { counts: { verts: 1, faces: 1 }, verts_url: '', faces_url: '', normals_url: '' },
      directions: [], fields: [],
      results: [{ process: 'injection_molding', analysis: 'thickness', hash: 'x', params: {}, stats: { min: 0.62, p50: 2.4 }, fields: [] }],
      highlights_url: null,
    },
    stats: '18 of 2043 faces below 1 mm · auto max 3.10 mm',
    legend: [
      { color: [0.87, 0.9, 0.92], label: 'thick — ok' },
      { color: RAMP1, label: '≤ 1.00 mm — flagged' },
      { color: [0.28, 0.32, 0.38], label: 'no data' },
    ],
  });
});
await page.waitForTimeout(500);

const legendText = await page.locator('.absolute.bottom-3.left-3').innerText().catch(() => '(no legend)');
console.log('LEGEND:\n' + legendText);
await page.screenshot({ path: (process.env.SHOT_DIR || '/tmp') + '/v2_legend.png' });

// dark mode too — status palette is fixed, ramp must still read on the dark card
await page.evaluate(() => document.documentElement.classList.add('dark'));
await page.waitForTimeout(300);
await page.screenshot({ path: (process.env.SHOT_DIR || '/tmp') + '/v2_legend_dark.png' });

await browser.close();
