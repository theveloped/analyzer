// Persistence + seeding for the client-side direction setup. The setup is the
// source of truth (arrows are generated from it live); it is kept per-part in
// localStorage so it survives reloads without any backend write. Accessibility
// is a separate, deferred concern and is never triggered from here.

import type { DirectionSource } from '../../api/types';
import type { RGB } from '../../registry/types';
import type { DirectionSetup } from './build';
import { EMPTY_SETUP } from './build';
import { PROVENANCE_COLORS } from './modes';

const KEY = (partId: string) => `directions-setup:${partId}`;

/** Best-effort seed from a part's stored provenance (for parts whose set was
 * computed by the backend before this editor existed). */
export function seedFromSources(sources: DirectionSource[]): DirectionSetup {
  const manual = new Map<string, number[]>();
  const groups = new Map<number, number[]>();
  let uniform = 0, axes = false, bbox = false, hole = false;
  for (const s of sources) {
    if (s.source === 'uniform') uniform++;
    else if (s.source === 'principal_axis') axes = true;
    else if (s.source === 'bbox_axis') bbox = true;
    else if (s.source === 'hole_axis') hole = true;
    else if (s.source === 'manual' && s.detail?.vector) {
      manual.set(JSON.stringify(s.detail.vector), s.detail.vector);
    } else if ((s.source === 'face_normal' || s.source === 'average_normal')
      && Array.isArray(s.detail?.brep_faces)) {
      groups.set(s.detail.group ?? groups.size, s.detail.brep_faces);
    }
  }
  return {
    ...EMPTY_SETUP,
    count: uniform ? Math.max(0, Math.round(uniform / 2)) : 0,
    axes, bboxAxes: bbox, holeAxes: hole,
    manual: [...manual.values()], brepGroups: [...groups.values()],
  };
}

/** The setup for a part: localStorage if present, else seeded from provenance. */
export function loadSetup(partId: string, sources: DirectionSource[]): DirectionSetup {
  try {
    const raw = localStorage.getItem(KEY(partId));
    if (raw) return { ...EMPTY_SETUP, ...JSON.parse(raw) };
  } catch { /* ignore malformed / unavailable storage */ }
  return seedFromSources(sources);
}

export function saveSetup(partId: string, setup: DirectionSetup): void {
  try {
    localStorage.setItem(KEY(partId), JSON.stringify({
      count: setup.count, axes: setup.axes, bboxAxes: setup.bboxAxes,
      holeAxes: setup.holeAxes, manual: setup.manual,
      brepGroups: setup.brepGroups, suppressed: setup.suppressed,
    }));
  } catch { /* storage full / unavailable — the in-memory setup still works */ }
}

/** A provenance color as a CSS rgb() string (for legend swatches). */
export function provenanceCss(source: keyof typeof PROVENANCE_COLORS): string {
  const c: RGB = PROVENANCE_COLORS[source] ?? PROVENANCE_COLORS.uniform;
  return `rgb(${Math.round(c[0] * 255)}, ${Math.round(c[1] * 255)}, ${Math.round(c[2] * 255)})`;
}
