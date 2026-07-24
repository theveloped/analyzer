import type { RGB } from '../../registry/types';

/**
 * A stable, distinct colour per datum letter, shared by the PMI painter (face
 * tint), the control-frame datum cells (background), the scope chips, and the
 * 3D callouts — so datum A reads the same everywhere. Letters cycle through the
 * palette (A→0, B→1, …); unknown/blank letters fall back to the first hue.
 */
const PALETTE: RGB[] = [
  [0.13, 0.63, 0.60], // teal
  [0.90, 0.55, 0.15], // amber
  [0.55, 0.40, 0.85], // violet
  [0.30, 0.70, 0.35], // green
  [0.85, 0.35, 0.55], // pink
  [0.25, 0.55, 0.90], // blue
  [0.62, 0.47, 0.28], // brown
  [0.20, 0.72, 0.80], // cyan
];

function datumIndex(letter: string | null | undefined): number {
  if (!letter) return 0;
  const c = letter.trim().toUpperCase().charCodeAt(0);
  if (c < 65 || c > 90) return 0;
  return (c - 65) % PALETTE.length;
}

export function datumColorRGB(letter: string | null | undefined): RGB {
  return PALETTE[datumIndex(letter)];
}

export function datumColorCss(letter: string | null | undefined, alpha = 1): string {
  const [r, g, b] = datumColorRGB(letter);
  const to = (v: number) => Math.round(v * 255);
  return alpha >= 1
    ? `rgb(${to(r)}, ${to(g)}, ${to(b)})`
    : `rgba(${to(r)}, ${to(g)}, ${to(b)}, ${alpha})`;
}
