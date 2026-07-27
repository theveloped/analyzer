import { segmentIdColor } from '../../colorizers/core';
import type { RGB } from '../../registry/types';

/**
 * A stable, distinct colour per datum letter, drawn from the SAME golden-ratio
 * segmentation palette the BREP-faces lens uses (`segmentIdColor`). Slots 0 and
 * 1 are reserved for toleranced / dimensioned features, so datums take slots
 * 2+ (A→2, B→3, …) and never share a colour with a control-frame's referenced
 * features. Shared by the painter, the control-frame datum cells, the scope
 * chips and the 3D callouts, so datum A reads the same everywhere.
 */
const DATUM_SLOT_OFFSET = 2;

function datumIndex(letter: string | null | undefined): number {
  if (!letter) return 0;
  const c = letter.trim().toUpperCase().charCodeAt(0);
  if (c < 65 || c > 90) return 0;
  return c - 65; // A → 0
}

export function datumColorRGB(letter: string | null | undefined): RGB {
  return segmentIdColor(datumIndex(letter) + DATUM_SLOT_OFFSET);
}

export function datumColorCss(letter: string | null | undefined, alpha = 1): string {
  const [r, g, b] = datumColorRGB(letter);
  const to = (v: number) => Math.round(v * 255);
  return alpha >= 1
    ? `rgb(${to(r)}, ${to(g)}, ${to(b)})`
    : `rgba(${to(r)}, ${to(g)}, ${to(b)}, ${alpha})`;
}
