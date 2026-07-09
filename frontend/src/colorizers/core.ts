// Process-agnostic painting building blocks: the shared palette, the turbo
// ramp, per-face reductions of per-vertex fields, and generic mode
// factories (mask / highlights) any plugin can reuse.

import type { FieldDescriptor } from '../api/types';
import type { LegendEntry, RGB, ViewCtx, ViewMode } from '../registry/types';

export const COL = {
  ok: [0.87, 0.9, 0.92] as RGB,
  inaccess: [0.28, 0.32, 0.38] as RGB,
  tip: [0.88, 0.29, 0.23] as RGB,
  holder: [0.95, 0.66, 0.23] as RGB,
  side: [0.44, 0.64, 0.86] as RGB,
  zmapOnly: [0.95, 0.66, 0.23] as RGB,
  voxelOnly: [0.26, 0.52, 0.96] as RGB,
  both: [0.88, 0.29, 0.23] as RGB,
  below: [0.87, 0.9, 0.92] as RGB,
  floor: [0.62, 0.8, 0.58] as RGB,
  slope: [0.38, 0.68, 0.66] as RGB,
  overhang: [0.72, 0.42, 0.55] as RGB,
};

// compact turbo-like ramp for scalar fields
export function rampColor(t: number): RGB {
  t = Math.min(1, Math.max(0, t));
  const stops = [
    [0.19, 0.07, 0.23], [0.25, 0.27, 0.67], [0.17, 0.69, 0.5],
    [0.99, 0.81, 0.22], [0.94, 0.36, 0.07], [0.48, 0.02, 0.01],
  ];
  const x = t * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i;
  return [0, 1, 2].map((k) => stops[i][k] * (1 - f) + stops[i + 1][k] * f) as unknown as RGB;
}

export function percentile(arr: Float32Array, p: number): number {
  const sample: number[] = [];
  const step = Math.max(1, Math.floor(arr.length / 5000));
  for (let i = 0; i < arr.length; i += step) if (isFinite(arr[i])) sample.push(arr[i]);
  sample.sort((x, y) => x - y);
  return sample.length ? sample[Math.min(sample.length - 1, Math.floor(p * sample.length))] : 1;
}

/** Face verdict from a per-vertex boolean lambda. */
export function faceBlocked(
  ctx: ViewCtx, f: number, blockedAt: (v: number) => boolean, rule: string,
): boolean {
  const a = blockedAt(ctx.faces[3 * f]);
  const b = blockedAt(ctx.faces[3 * f + 1]);
  const c = blockedAt(ctx.faces[3 * f + 2]);
  return rule === 'all' ? a && b && c : a || b || c;
}

/** Per-face mean of a per-vertex field, restricted to unmasked faces. */
export function faceValues(
  ctx: ViewCtx, field: Float32Array, keep: ((f: number) => boolean) | null,
): Float32Array {
  const out = new Float32Array(ctx.faceCount).fill(NaN);
  for (let f = 0; f < ctx.faceCount; f++) {
    if (keep && !keep(f)) continue;
    out[f] = (field[ctx.faces[3 * f]] + field[ctx.faces[3 * f + 1]] + field[ctx.faces[3 * f + 2]]) / 3;
  }
  return out;
}

/**
 * Per-face angle (degrees) between the outward normal and an approach
 * direction: 0 = floor seen straight on, 90 = vertical wall, >90 = overhang.
 */
export function faceAngles(ctx: ViewCtx, direction: number[]): Float32Array {
  const out = new Float32Array(ctx.faceCount);
  for (let f = 0; f < ctx.faceCount; f++) {
    const dot = ctx.normals[3 * f] * direction[0]
      + ctx.normals[3 * f + 1] * direction[1]
      + ctx.normals[3 * f + 2] * direction[2];
    out[f] = (Math.acos(Math.min(1, Math.max(-1, dot))) * 180) / Math.PI;
  }
  return out;
}

/** Generic mask painter: on/off face field with a two-entry legend. */
export function paintMask(
  ctx: ViewCtx, mask: Uint8Array | Float32Array,
  onColor: RGB, offColor: RGB, onLabel: string, offLabel: string,
): { legend: LegendEntry[]; stats: string } {
  let n = 0;
  ctx.paintFaces((f) => (mask[f] ? (n++, onColor) : offColor));
  return {
    legend: [
      { color: onColor, label: onLabel },
      { color: offColor, label: offLabel },
    ],
    stats: `${n} of ${ctx.faceCount} faces`,
  };
}

/** Generic mask view mode over one face-associated mask field. */
export function maskMode(
  id: string, label: string,
  pickField: (ctx: ViewCtx) => FieldDescriptor | null,
  labels?: { on?: string; off?: string },
): ViewMode {
  return {
    id,
    label,
    async paint(ctx) {
      const desc = pickField(ctx);
      if (!desc) throw new Error('no matching field cached for this view');
      const mask = await ctx.getField(desc);
      return paintMask(ctx, mask, COL.ok, COL.inaccess,
        labels?.on ?? 'in set', labels?.off ?? 'not in set');
    },
  };
}

/** "Last CLI highlights.json" — process-agnostic replay of the legacy result. */
export const highlightsMode: ViewMode = {
  id: 'highlights',
  label: 'Last CLI highlights.json',
  async paint(ctx) {
    if (!ctx.highlights) throw new Error('no highlights.json in the working directory');
    const flagged = new Set(ctx.highlights);
    ctx.paintFaces((f) => (flagged.has(f) ? COL.tip : COL.ok));
    return {
      legend: [
        { color: COL.tip, label: `flagged by last CLI run (${ctx.highlights.length} faces)` },
        { color: COL.ok, label: 'not flagged' },
      ],
    };
  },
};
