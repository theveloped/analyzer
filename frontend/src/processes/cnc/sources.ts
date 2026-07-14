// Group the manifest's flat field list back into per-direction "sources" —
// the working unit of the CNC views: one direction cache with its tip gap
// fields, clearance fields and tip-aware stickout fields.

import type { FieldDescriptor, Manifest } from '../../api/types';

export interface TipEntry {
  diameter: number;
  corner_radius: number;
  field: FieldDescriptor;
  stickouts: { radius: number; field: FieldDescriptor }[];
}

export interface CncSource {
  key: string;
  direction: number;
  pixel: number | null;
  tips: TipEntry[];
  clearances: { radius: number; field: FieldDescriptor }[];
  accessibility: FieldDescriptor | null;
}

export function cncSources(manifest: Manifest): CncSource[] {
  const sources = new Map<number, CncSource>();
  const access = new Map<number, FieldDescriptor>();

  for (const field of manifest.fields) {
    const p = field.params;
    if (p.kind === 'accessibility') {
      access.set(p.direction, field);
      continue;
    }
    if (!['tip_gap', 'clearance', 'min_stickout'].includes(p.kind)) continue;
    if (!sources.has(p.direction)) {
      sources.set(p.direction, {
        key: `dir${p.direction}`,
        direction: p.direction,
        pixel: p.pixel ?? null,
        tips: [],
        clearances: [],
        accessibility: null,
      });
    }
    const source = sources.get(p.direction)!;
    if (p.kind === 'tip_gap') {
      source.tips.push({
        diameter: p.diameter, corner_radius: p.corner_radius, field, stickouts: [],
      });
    } else if (p.kind === 'clearance') {
      source.clearances.push({ radius: p.radius, field });
    }
  }

  // attach tip-aware stickout fields to their tips
  for (const field of manifest.fields) {
    const p = field.params;
    if (p.kind !== 'min_stickout') continue;
    const tip = sources.get(p.direction)?.tips.find(
      (t) => t.diameter === p.diameter && t.corner_radius === p.corner_radius);
    if (tip) tip.stickouts.push({ radius: p.radius, field });
  }

  // directions with accessibility but no tool fields still get a (bare)
  // source, so the access/class views work right after prep/directions
  for (const direction of access.keys()) {
    if (sources.has(direction)) continue;
    sources.set(direction, {
      key: `dir${direction}`, direction, pixel: null,
      tips: [], clearances: [], accessibility: null,
    });
  }

  const list = [...sources.values()];
  for (const source of list) {
    source.tips.sort((a, b) => a.diameter - b.diameter || a.corner_radius - b.corner_radius);
    source.clearances.sort((a, b) => a.radius - b.radius);
    for (const tip of source.tips) tip.stickouts.sort((a, b) => a.radius - b.radius);
    source.accessibility = access.get(source.direction) ?? null;
  }
  // tool-field sources first: params.source defaults to 0 and the default
  // (unified) mode needs tips, so bare accessibility sources go last
  const hasFields = (s: CncSource) => (s.tips.length + s.clearances.length > 0 ? 1 : 0);
  list.sort((a, b) => hasFields(b) - hasFields(a) || a.direction - b.direction);
  return list;
}

export function currentSource(manifest: Manifest, params: Record<string, any>): CncSource | null {
  const sources = cncSources(manifest);
  return sources[params.source] ?? sources[0] ?? null;
}

export function currentTip(source: CncSource | null, params: Record<string, any>): TipEntry | null {
  if (!source || !source.tips.length) return null;
  return source.tips[params.tip] ?? source.tips[0];
}

export function holderCylinders(text: string): { radius: number; start: number }[] {
  if (!text?.trim()) return [];
  return text.split(',').map((part) => {
    const [r, s] = part.split(':').map(Number);
    return { radius: r, start: s || 0 };
  }).filter((c) => isFinite(c.radius) && c.radius > 0);
}

/**
 * Gap threshold for near-90° walls: reachable walls carry ~1 pixel of
 * height-map quantization noise, unreachable ones (e.g. inside a slot
 * narrower than the tool) sit whole millimetres inside the closed solid,
 * so a few pixels cleanly separates them.
 */
export function wallThreshold(source: CncSource, tol: number): number {
  return Math.max(tol, 2.5 * (source.pixel || tol));
}
