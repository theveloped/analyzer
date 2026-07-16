// Process-agnostic painting building blocks: the shared palette, the turbo
// ramp, per-face reductions of per-vertex fields, and generic mode
// factories (mask / highlights) any plugin can reuse.

import type { FieldDescriptor } from '../api/types';
import type {
  LegendEntry, LegendFocus, RGB, ViewCtx, ViewMode,
} from '../registry/types';

export const COL = {
  ok: [0.87, 0.9, 0.92] as RGB,
  inaccess: [0.28, 0.32, 0.38] as RGB,
  tip: [0.88, 0.29, 0.23] as RGB,
  holder: [0.95, 0.66, 0.23] as RGB,
  side: [0.44, 0.64, 0.86] as RGB,
  below: [0.87, 0.9, 0.92] as RGB,
  floor: [0.62, 0.8, 0.58] as RGB,
  slope: [0.38, 0.68, 0.66] as RGB,
  overhang: [0.72, 0.42, 0.55] as RGB,
};

// --- bitmask helpers for membership/assignment fields ---

/** Washed-out version of a category color, for non-selected stripes. */
export function fade(color: RGB): RGB {
  return [color[0] * 0.45 + 0.55, color[1] * 0.45 + 0.55, color[2] * 0.45 + 0.55];
}

export function popcount(x: number): number {
  let n = 0;
  while (x) { n += x & 1; x >>>= 1; }
  return n;
}

export function nthSetBit(x: number, n: number): number {
  for (let bit = 0; bit < 32; bit++) {
    if ((x >>> bit) & 1) {
      if (n === 0) return bit;
      n--;
    }
  }
  return 0;
}

export function nextSetBit(x: number, after: number): number {
  for (let bit = after + 1; bit < 32; bit++) if ((x >>> bit) & 1) return bit;
  for (let bit = 0; bit <= after; bit++) if ((x >>> bit) & 1) return bit;
  return after;
}

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
 * Marching-triangles isolines of a per-vertex scalar field: for every face
 * each crossing level contributes one segment (linear interpolation on the
 * edges). `lift` pushes the segments off the surface along the face normal
 * so depth-tested lines never z-fight with the mesh. Returns flattened
 * endpoint pairs for ViewCtx.setLines.
 */
export function isolineSegments(
  ctx: ViewCtx, field: Float32Array, levels: number[], lift = 0,
): Float32Array {
  const { verts, faces, normals } = ctx;
  const segments: number[] = [];
  for (let f = 0; f < ctx.faceCount; f++) {
    const a = faces[3 * f];
    const b = faces[3 * f + 1];
    const c = faces[3 * f + 2];
    const va = field[a];
    const vb = field[b];
    const vc = field[c];
    if (isNaN(va) || isNaN(vb) || isNaN(vc)) continue;
    const lo = Math.min(va, vb, vc);
    const hi = Math.max(va, vb, vc);
    const lx = lift * normals[3 * f];
    const ly = lift * normals[3 * f + 1];
    const lz = lift * normals[3 * f + 2];
    for (const level of levels) {
      if (level <= lo || level > hi) continue;
      const points: number[] = [];
      const cross = (i: number, j: number, vi: number, vj: number) => {
        if ((vi < level) === (vj < level)) return;
        const t = (level - vi) / (vj - vi);
        points.push(
          verts[3 * i] + t * (verts[3 * j] - verts[3 * i]) + lx,
          verts[3 * i + 1] + t * (verts[3 * j + 1] - verts[3 * i + 1]) + ly,
          verts[3 * i + 2] + t * (verts[3 * j + 2] - verts[3 * i + 2]) + lz,
        );
      };
      cross(a, b, va, vb);
      cross(b, c, vb, vc);
      cross(c, a, vc, va);
      if (points.length === 6) segments.push(...points);
    }
  }
  return new Float32Array(segments);
}

/**
 * NaN-aware Laplacian smoothing of a per-vertex field over the mesh edges —
 * takes the node-quantized fill times to a continuous field so gradients
 * and isolines read cleanly. NaN vertices stay NaN and are ignored as
 * neighbors.
 */
export function smoothVertexField(
  ctx: ViewCtx, field: Float32Array, iterations = 2,
): Float32Array {
  const { faces } = ctx;
  let current = field;
  for (let it = 0; it < iterations; it++) {
    const sum = new Float32Array(field.length);
    const count = new Int32Array(field.length);
    const edge = (i: number, j: number) => {
      const vi = current[i];
      const vj = current[j];
      if (isNaN(vi) || isNaN(vj)) return;
      sum[i] += vj;
      count[i]++;
      sum[j] += vi;
      count[j]++;
    };
    for (let f = 0; f < ctx.faceCount; f++) {
      const a = faces[3 * f];
      const b = faces[3 * f + 1];
      const c = faces[3 * f + 2];
      edge(a, b);
      edge(b, c);
      edge(c, a);
    }
    const next = new Float32Array(field.length);
    for (let v = 0; v < field.length; v++) {
      next[v] = count[v]
        ? (current[v] + sum[v] / count[v]) / 2
        : current[v];
    }
    current = next;
  }
  return current;
}

/**
 * Accumulates per-group face statistics into LegendFocus entries: bounding
 * box center to aim at, area-weighted mean normal to look from, half the
 * bbox diagonal as the size. One `add(key, f)` call per painted face.
 */
export class FocusTracker {
  private groups = new Map<string, {
    n: number; nx: number; ny: number; nz: number;
    min: [number, number, number]; max: [number, number, number];
  }>();

  constructor(private ctx: ViewCtx) {}

  add(key: string, f: number) {
    let g = this.groups.get(key);
    if (!g) {
      g = {
        n: 0, nx: 0, ny: 0, nz: 0,
        min: [Infinity, Infinity, Infinity],
        max: [-Infinity, -Infinity, -Infinity],
      };
      this.groups.set(key, g);
    }
    const { verts, faces, normals } = this.ctx;
    g.n++;
    g.nx += normals[3 * f];
    g.ny += normals[3 * f + 1];
    g.nz += normals[3 * f + 2];
    for (let k = 0; k < 3; k++) {
      const v = faces[3 * f + k];
      for (let axis = 0; axis < 3; axis++) {
        const value = verts[3 * v + axis];
        if (value < g.min[axis]) g.min[axis] = value;
        if (value > g.max[axis]) g.max[axis] = value;
      }
    }
  }

  count(key: string): number {
    return this.groups.get(key)?.n ?? 0;
  }

  focus(key: string): LegendFocus | undefined {
    const g = this.groups.get(key);
    if (!g || !g.n) return undefined;
    const dx = g.max[0] - g.min[0];
    const dy = g.max[1] - g.min[1];
    const dz = g.max[2] - g.min[2];
    return {
      center: [(g.min[0] + g.max[0]) / 2, (g.min[1] + g.max[1]) / 2,
               (g.min[2] + g.max[2]) / 2],
      direction: [g.nx / g.n, g.ny / g.n, g.nz / g.n],
      radius: Math.sqrt(dx * dx + dy * dy + dz * dz) / 2,
    };
  }

  /** All group keys with the given prefix, e.g. one per undercut region. */
  keys(prefix: string): string[] {
    return [...this.groups.keys()].filter((k) => k.startsWith(prefix));
  }

  /** Merged focus over several groups (e.g. all tiny speck regions). */
  merged(keys: string[]): LegendFocus | undefined {
    let out: LegendFocus | undefined;
    let n = 0;
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    const normal = [0, 0, 0];
    for (const key of keys) {
      const g = this.groups.get(key);
      if (!g) continue;
      n += g.n;
      normal[0] += g.nx;
      normal[1] += g.ny;
      normal[2] += g.nz;
      for (let axis = 0; axis < 3; axis++) {
        min[axis] = Math.min(min[axis], g.min[axis]);
        max[axis] = Math.max(max[axis], g.max[axis]);
      }
    }
    if (n) {
      const dx = max[0] - min[0];
      const dy = max[1] - min[1];
      const dz = max[2] - min[2];
      out = {
        center: [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2,
                 (min[2] + max[2]) / 2],
        direction: [normal[0] / n, normal[1] / n, normal[2] / n],
        radius: Math.sqrt(dx * dx + dy * dy + dz * dz) / 2,
      };
    }
    return out;
  }
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
  ctx: ViewCtx, mask: Uint8Array | Float32Array | Uint32Array,
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

export interface HeatmapOpts {
  /** Per-VERTEX transform applied before the per-face mean; NaN masks out. */
  transform?: (v: number) => number;
  thresholdParam?: string; // ctx.params key, default 'threshold'
  scaleParam?: string; // ctx.params key, default 'scale'
  /** 'above': high values are bad (gaps to material). 'below': low values
      are bad (thin walls, tight clearance). Default 'above'. */
  flagDirection?: 'above' | 'below';
  units?: string;
  autoFloor?: number; // lower bound for the percentile auto-max
  okLabel?: string;
  maskedLabel?: string;
  /** Per-VERTEX mask (1 = reading explainable by local sharp geometry).
      Faces touching an excluded vertex never count as flagged (matching
      the CLI's all-verts-thin rule, where one excluded vertex vetoes the
      face). */
  exclusion?: (ctx: ViewCtx) => Promise<Uint8Array | null>;
  /** ctx.params key of a boolean: when true, excluded below-threshold
      faces are painted in the ok color instead of their heatmap color. */
  maskParam?: string;
}

/**
 * Generic per-vertex scalar heatmap over one field, with a client-side
 * threshold. (The CNC gap/stickout modes predate this factory and keep
 * their angle-dependent thresholds and rule-based counting — folding them
 * onto this is a possible follow-up.)
 */
export function heatmapMode(
  id: string, label: string,
  pickField: (ctx: ViewCtx) => FieldDescriptor | null,
  opts: HeatmapOpts = {},
): ViewMode {
  const parse = (value: any) => {
    const parsed = parseFloat(value);
    return isFinite(parsed) ? parsed : NaN;
  };
  return {
    id,
    label,
    async paint(ctx) {
      const desc = pickField(ctx);
      if (!desc) {
        throw new Error(`no cached field for "${label}" — run the analysis in the Compute panel`);
      }
      const raw = await ctx.getField(desc) as Float32Array;
      const field = opts.transform ? Float32Array.from(raw, opts.transform) : raw;
      const vals = faceValues(ctx, field, null);
      const excluded = opts.exclusion ? await opts.exclusion(ctx) : null;
      const thr = parse(ctx.params[opts.thresholdParam ?? 'threshold']) || 0;
      const auto = Math.max(percentile(vals, 0.98), opts.autoFloor ?? thr * 3, 1e-6);
      const max = parse(ctx.params[opts.scaleParam ?? 'scale']) || auto;
      const below = opts.flagDirection === 'below';
      const span = Math.max(max - thr, 1e-9);
      const units = opts.units ?? 'mm';
      const maskOn = opts.maskParam ? ctx.params[opts.maskParam] !== false : false;
      let flagged = 0;
      let explained = 0;
      let painted = 0;
      ctx.paintFaces((f) => {
        const v = vals[f];
        if (isNaN(v)) return COL.inaccess;
        painted++;
        if (below ? v <= thr : v > thr) {
          // one excluded vertex vetoes the face — same as the CLI's
          // all-verts-thin flag rule, and it dilates the mask by the one
          // boundary facet the vertex mask alone would miss
          const skip = excluded
            && (excluded[ctx.faces[3 * f]]
              || excluded[ctx.faces[3 * f + 1]]
              || excluded[ctx.faces[3 * f + 2]]);
          if (skip) {
            explained++;
            if (maskOn) return COL.below;
          } else {
            flagged++;
          }
        }
        const badness = below ? (max - v) / span : (v - thr) / span;
        return badness <= 0 ? COL.below : rampColor(Math.min(1, badness));
      });
      const legend: LegendEntry[] = [
        {
          color: COL.below,
          label: opts.okLabel
            ?? (below ? `≥ ${max.toFixed(2)} ${units} — ok` : `≤ ${thr.toFixed(2)} ${units} — ok`),
        },
        {
          color: rampColor(1),
          label: below ? `≤ ${thr.toFixed(2)} ${units} — flagged` : `≥ ${max.toFixed(2)} ${units}`,
        },
        ...(opts.exclusion && maskOn ? [{
          color: COL.below,
          label: 'explained by sharp edges — shown as ok',
        }] : []),
        { color: COL.inaccess, label: opts.maskedLabel ?? 'no data' },
      ];
      return {
        legend,
        stats: `${flagged} of ${painted} faces ${below ? 'below' : 'above'} ${thr} ${units}`
          + (explained ? ` (${explained} more explained by sharp edges — not flagged)` : '')
          + ` · auto max ${auto.toFixed(2)} ${units}`,
      };
    },
  };
}

/** Paint a per-face u1 category field with labels/colors indexed by code. */
export function paintCategory(
  ctx: ViewCtx, values: Uint8Array, labels: string[], colors: RGB[],
): { legend: LegendEntry[]; stats: string } {
  const counts = new Array(labels.length).fill(0);
  ctx.paintFaces((f) => {
    const code = values[f];
    if (code < counts.length) counts[code]++;
    return colors[code] ?? COL.inaccess;
  });
  return {
    legend: labels
      .map((label, i) => ({ color: colors[i] ?? COL.inaccess, label: `${label} (${counts[i]})` }))
      .filter((_, i) => counts[i] > 0),
    stats: '',
  };
}

function hsl(h: number, s: number, l: number): RGB {
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const channel = (t: number) => {
    t = ((t % 1) + 1) % 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return [channel(h + 1 / 3), channel(h), channel(h - 1 / 3)];
}

/** Golden-ratio hue for stable, well-separated per-id colors. */
export function segmentIdColor(id: number): RGB {
  return hsl((id * 0.618034) % 1, 0.5, 0.62);
}

/** Red-family per-region color for numbered internal undercut regions. */
export function regionColor(region: number): RGB {
  const t = (region * 0.618034) % 1;
  return hsl(0.98 + 0.06 * t, 0.62, 0.4 + 0.28 * t);
}

/** Source BREP faces (from the STEP-aware mesher), one color per face id.
 * User face splits show as their sub-face pieces when present. */
export const brepFacesMode: ViewMode = {
  id: 'brep_faces',
  label: 'BREP faces',
  async paint(ctx) {
    const desc = ctx.manifest.fields.find((f) => f.id === 'subfaces')
      ?? ctx.manifest.fields.find((f) => f.id === 'brep_faces');
    if (!desc) {
      throw new Error('no BREP face ids — re-mesh the part from its STEP file');
    }
    const ids = await ctx.getField(desc) as Uint32Array;
    ctx.paintFaces((f) => segmentIdColor(ids[f]));
    const split = desc.params.n_brep != null
      ? ` (${desc.params.n_brep} BREP + user cuts)` : '';
    return {
      legend: [],
      stats: `${desc.params.count} faces${split} over ${ctx.faceCount} triangles`,
    };
  },
};

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
