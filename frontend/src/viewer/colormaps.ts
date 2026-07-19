// Canonical color system for the viewer and UI — one source of truth, using
// official perceptually-uniform, CVD-safe scientific colour maps (Crameri +
// matplotlib) rather than hand-rolled ramps. Each field "job" gets the map the
// literature prescribes, and — because these maps span dark→light — a
// background-matched variant so the informative end never sinks into the
// viewer background:
//
//   magnitude (unsigned)  sequential  batlowW (dark bg) / batlowK (light bg)
//   signed deviation      diverging   vik   (dark bg) / berlin  (light bg)
//   risk / severity       sequential  inferno (both)
//   segmented areas       categorical validated 8-set / OKLCH generator
//   reserved              status / highlight / selection (icon+label, tokens)
//
// Colors are RGB in 0..1 to match the viewer's paintFaces contract; CSS helpers
// render the same values so a legend always equals its surface.

import type { RGB } from '../registry/types';

export type ViewerBackground = 'dark' | 'light';

let BG: ViewerBackground = 'dark';
/** Switch which background-matched variant the maps resolve to. */
export function setColorBackground(bg: ViewerBackground): void { BG = bg; }
export function colorBackground(): ViewerBackground { return BG; }

/** Viewer clear color per theme (the three.js scene background). */
export const VIEWER_BG: Record<ViewerBackground, string> = {
  dark: '#21262c',
  light: '#e9ecf0',
};

function hex(h: string): RGB {
  const n = parseInt(h.slice(1), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

function lerp(a: RGB, b: RGB, f: number): RGB {
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

/** Sample evenly-spaced stops at t∈[0,1]. */
function sampleEven(stops: RGB[], t: number): RGB {
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  return lerp(stops[i], stops[i + 1], x - i);
}

// ── Official LUTs (downsampled to smooth anchors; interpolation is
// imperceptible at this density since the source maps are perceptually even) ──

// batlow with a WHITE top — the light high end pops on the dark viewer.
export const BATLOW_W: RGB[] = ['#011959','#0d335e','#114360','#28655f','#437254','#687f41','#948f32','#ba9333','#d8a566','#edaf8f','#f8bdaf','#fed1cd','#ffe8e7','#fff6f6','#fffefe'].map(hex);
// batlow with a BLACK bottom — the dark low end pops on a light viewer.
export const BATLOW_K: RGB[] = ['#04050a','#131e2d','#21384f','#33505e','#49625a','#63724b','#83813d','#a38e38','#c29840','#de9f55','#f1a678','#fcb2aa','#fdbac3','#fdbfd3','#faccfa'].map(hex);
// viridis (matplotlib) — perceptually-uniform, dark-purple → yellow. Same map
// on both backgrounds (its dark low end suits the dark viewer).
const VIRIDIS: RGB[] = ['#440154','#482878','#3e4a89','#31688e','#26828e','#1f9e89','#35b779','#6ece58','#b5de2b','#fde725'].map(hex);

// vik: dark-blue ↔ WHITE centre ↔ dark-red. Light centre = zero visible on dark.
const VIK: RGB[] = ['#001261','#022e73','#034280','#136697','#2b79a4','#4e92b4','#74aac5','#9ac2d5','#c0d8e4','#e7e7e7','#eee3dc','#d9a486','#b08056','#af4310','#590008'].map(hex);
const VIK_NEUTRAL = 9; // index of the near-white centre
// berlin: light-blue ↔ near-BLACK centre ↔ light-red. Dark centre = zero visible on light.
const BERLIN: RGB[] = ['#9eb0ff','#74aaeb','#60a5df','#122c38','#170d0b','#1d0b05','#280d01','#3c1101','#501803','#732b16','#964a36','#ba6b5f','#df8f89','#ffadad'].map(hex);
const BERLIN_NEUTRAL = 4; // index of the darkest (centre) stop

// inferno: black → purple → red → yellow. Severe = bright, pops on dark; low
// risk recedes. Used on both backgrounds for now.
const INFERNO: RGB[] = ['#000004','#130a30','#340a5f','#55106d','#751b6e','#942568','#b3325a','#d04545','#e55c30','#f57d15','#fca80d','#fac62d','#fafda1'].map(hex);

/** Unsigned magnitude (thickness, gap, clearance). t∈[0,1]. */
export function sequential(t: number): RGB {
  // trying viridis; revert to `BG === 'light' ? BATLOW_K : BATLOW_W` for batlow
  return sampleEven(VIRIDIS, t);
}

/** Signed deviation with a meaningful zero (draft ±, over/under a target).
 * s∈[-1,1], 0 = neutral (pinned to the map's neutral stop). */
export function diverging(s: number): RGB {
  const stops = BG === 'light' ? BERLIN : VIK;
  const neutral = BG === 'light' ? BERLIN_NEUTRAL : VIK_NEUTRAL;
  const clamped = Math.max(-1, Math.min(1, s));
  const pos = clamped <= 0
    ? (clamped + 1) * neutral
    : neutral + clamped * (stops.length - 1 - neutral);
  const i = Math.min(stops.length - 2, Math.max(0, Math.floor(pos)));
  return lerp(stops[i], stops[i + 1], pos - i);
}

/** Risk / severity, where high = worse. t∈[0,1]. */
export function severity(t: number): RGB {
  return sampleEven(INFERNO, t);
}

// ── Categorical: validated dataviz 8-hue set (dark steps) for few named
// segments; an OKLCH golden-angle generator for many BREP faces. ──
export const CATEGORICAL: RGB[] = [
  '#3987e5', '#008300', '#d55181', '#c98500',
  '#199e70', '#d95926', '#9085e9', '#e66767',
].map(hex);

export function categorical(i: number): RGB {
  return i < CATEGORICAL.length ? CATEGORICAL[i] : segment(i);
}

export function segment(id: number): RGB {
  return oklchToRgb(0.62, 0.13, (id * 137.508) % 360);
}

// ── Reserved semantic colors (match the app.css status tokens). ──
export const STATUS = {
  good: hex('#0ca30c'),
  warning: hex('#fab219'),
  serious: hex('#ec835a'),
  critical: hex('#d03b3b'),
} as const;

export const HIGHLIGHT = {
  correct: STATUS.good,
  incorrect: STATUS.critical,
  warn: STATUS.warning,
  selected: hex('#ffffff'), // outline, not a fill hue
} as const;

/** Neutral, unpainted mesh + no-data, per background so it never sinks in. */
export function meshBase(): RGB {
  return BG === 'light' ? [0.7, 0.73, 0.77] : [0.87, 0.9, 0.92];
}
export const MESH = {
  base: [0.87, 0.9, 0.92] as RGB,
  inaccessible: [0.28, 0.32, 0.38] as RGB,
};

export const EDGE = {
  parting: [1.0, 0.85, 0.2] as RGB,
  isoline: [0.08, 0.09, 0.11] as RGB,
  weld: [1.0, 0.3, 0.75] as RGB,
};

// ── CSS helpers — legends render the exact same values as the surface. ──
export function cssRGB(c: RGB | readonly number[]): string {
  return `rgb(${Math.round(c[0] * 255)} ${Math.round(c[1] * 255)} ${Math.round(c[2] * 255)})`;
}

function gradientCss(stops: RGB[]): string {
  return `linear-gradient(90deg, ${stops
    .map((c, i) => `${cssRGB(c)} ${Math.round((i / (stops.length - 1)) * 100)}%`)
    .join(', ')})`;
}

export const sequentialGradientCss = () => gradientCss(VIRIDIS);
export const severityGradientCss = () => gradientCss(INFERNO);

/** Diverging gradient sampled through the neutral-pinned `diverging()` map, so
 * s = 0 (neutral) lands at 50% — the bar reads with zero at the centre. */
export function divergingGradientCss(): string {
  const n = 21;
  const stops = Array.from({ length: n }, (_, i) => {
    const s = -1 + (2 * i) / (n - 1);
    return `${cssRGB(diverging(s))} ${Math.round((i / (n - 1)) * 100)}%`;
  });
  return `linear-gradient(90deg, ${stops.join(', ')})`;
}

// ── OKLCH → sRGB (for the unbounded segment generator) ──
function oklchToRgb(L: number, C: number, hDeg: number): RGB {
  const h = (hDeg * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3;
  const m = m_ ** 3;
  const s = s_ ** 3;
  const toSrgb = (x: number) => {
    const v = x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055;
    return Math.min(1, Math.max(0, v));
  };
  return [
    toSrgb(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    toSrgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    toSrgb(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  ];
}
