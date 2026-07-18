// Canonical color system for the viewer and UI — one validated source of
// truth for every "job" a color does here. Derived with the dataviz method and
// checked with its validator against the fixed three.js viewer background
// (#21262c); see docs at the bottom for the recorded verdicts.
//
// Four continuous/quantitative jobs + a reserved semantic set:
//   sequential(t)   unsigned magnitude   (thickness, clearance)      one hue, light→dark
//   diverging(t)    signed magnitude     (draft ±, over/under target) two hues + neutral 0
//   categorical(i)  few named segments   (≤8, legend)                fixed validated hues
//   segment(id)     many raw segments    (BREP face ids)             perceptual generator
//   STATUS/HIGHLIGHT reserved meanings   (ok/flag, too small/large, selection)
//
// Colors are RGB in 0..1 to match the viewer's paintFaces contract; CSS helpers
// render the same values for legends so a legend always equals its surface.

import type { RGB } from '../registry/types';

function hex(h: string): RGB {
  const n = parseInt(h.replace('#', ''), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

function lerp(a: RGB, b: RGB, f: number): RGB {
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

/** Sample a list of stops at t∈[0,1] (linear between adjacent stops). */
function sample(stops: RGB[], t: number): RGB {
  const clamped = Math.min(1, Math.max(0, t));
  const x = clamped * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  return lerp(stops[i], stops[i + 1], x - i);
}

// ── Sequential: single-hue blue, light→dark, perceptually-even lightness.
// Unsigned magnitude / severity. Light end recedes toward "near zero / at
// limit"; dark end is the extreme. (dataviz sequential job.)
export const SEQUENTIAL_STOPS: RGB[] = [
  '#d5eeff', '#bfddff', '#a9ccf9', '#93bcef', '#7eabe5', '#689bdb',
  '#538ad1', '#3c7ac6', '#236abc', '#0059b1', '#0048a6',
].map(hex);

export function sequential(t: number): RGB {
  return sample(SEQUENTIAL_STOPS, t);
}

// ── Diverging: red ← neutral → blue, light neutral midpoint (Moreland-style).
// Signed magnitude with a meaningful zero. t∈[-1,1]: −1 red pole, 0 neutral,
// +1 blue pole. Poles CVD-separate at ΔE 22.8. (dataviz diverging job.)
export const DIVERGING_STOPS: RGB[] = [
  '#b63b32', '#c46257', '#d0847a', '#daa59e', '#e2c6c2', '#e8e8e8',
  '#c0cfe3', '#99b7dd', '#729ed6', '#4a85ce', '#186bc5',
].map(hex);

/** Signed magnitude; t∈[-1,1], 0 = neutral. By convention negative → red
 * (e.g. undercut / below target), positive → blue (e.g. drafted / above). */
export function diverging(t: number): RGB {
  return sample(DIVERGING_STOPS, (Math.min(1, Math.max(-1, t)) + 1) / 2);
}

// ── Categorical: the dataviz 8-hue set, dark steps (the viewer bg is always
// dark). Fixed order IS the CVD-safety mechanism — assign in sequence, never
// cycle. Use for FEW, named segments shown in a legend. Passes all gates
// adjacent; only the first 4 clear the harder all-pairs test.
export const CATEGORICAL: RGB[] = [
  '#3987e5', '#008300', '#d55181', '#c98500',
  '#199e70', '#d95926', '#9085e9', '#e66767',
].map(hex);

/** i<8 → the fixed slot; i≥8 → the unbounded generator (see `segment`). */
export function categorical(i: number): RGB {
  return i < CATEGORICAL.length ? CATEGORICAL[i] : segment(i);
}

// ── Many raw segments (BREP face ids): no palette makes hundreds of touching
// patches legibly identifiable, so the goal is only "adjacent faces differ" +
// click-to-identify. Golden-angle hue in OKLCH at a fixed in-band lightness &
// chroma gives even, repeatable, well-separated hues. Replaces the old
// HSL golden-ratio generator (OKLCH keeps lightness perceptually constant).
export function segment(id: number): RGB {
  return oklchToRgb(0.62, 0.13, (id * 137.508) % 360);
}

// ── Reserved semantic colors (never used for series identity). Match the
// validated status tokens in app.css so mesh + chrome agree.
export const STATUS = {
  good: hex('#0ca30c'),
  warning: hex('#fab219'),
  serious: hex('#ec835a'),
  critical: hex('#d03b3b'),
} as const;

/** Discrete highlights on the mesh. Single-sided judgments use STATUS; a
 * signed bound (too small / too large) reuses the diverging poles so a signed
 * field and its flags share one language. Selection is a reserved white. */
export const HIGHLIGHT = {
  correct: STATUS.good,
  incorrect: STATUS.critical,
  warn: STATUS.warning,
  tooSmall: DIVERGING_STOPS[0], // red pole
  tooLarge: DIVERGING_STOPS[DIVERGING_STOPS.length - 1], // blue pole
  selected: hex('#ffffff'),
} as const;

/** Non-data surfaces: the neutral unpainted mesh and the "no data" gray. */
export const MESH = {
  base: [0.87, 0.9, 0.92] as RGB,
  inaccessible: [0.28, 0.32, 0.38] as RGB,
};

/** Overlay line colors, centralized so nothing re-picks them ad hoc. */
export const EDGE = {
  parting: [1.0, 0.85, 0.2] as RGB,
  isoline: [0.08, 0.09, 0.11] as RGB,
  weld: [1.0, 0.3, 0.75] as RGB,
};

// ── CSS helpers (legends render the exact same values as the surface) ──
export function cssRGB(c: RGB | readonly number[]): string {
  return `rgb(${Math.round(c[0] * 255)} ${Math.round(c[1] * 255)} ${Math.round(c[2] * 255)})`;
}

function gradientCss(stops: RGB[]): string {
  return `linear-gradient(90deg, ${stops
    .map((c, i) => `${cssRGB(c)} ${Math.round((i / (stops.length - 1)) * 100)}%`)
    .join(', ')})`;
}

export const sequentialGradientCss = () => gradientCss(SEQUENTIAL_STOPS);
export const divergingGradientCss = () => gradientCss(DIVERGING_STOPS);

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

/*
Validator verdicts (dataviz scripts/validate_palette.js, surface #21262c):
- CATEGORICAL, adjacent (stacks/legends): PASS — worst CVD ΔE 8.4, normal 19.3,
  all ≥3:1. Safe as a named ≤8 set.
- CATEGORICAL, --pairs all (any-two-adjacent, e.g. face ids): first 4 slots
  clear with secondary encoding (floor band 6.9); the full 8 cannot — so >4
  freely-adjacent segments must use `segment()` + click-to-identify, not hue.
- DIVERGING poles: PASS CVD ΔE 22.8 / normal 29.4; poles are mid-lightness
  (2.7–2.9:1 vs bg) so the legend is the required relief channel.
- SEQUENTIAL / DIVERGING ramps intentionally fail the categorical validator by
  design (they span the lightness band) — the ramp check is lightness
  monotonicity, which both satisfy.
*/
