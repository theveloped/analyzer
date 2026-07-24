// Process-agnostic painting building blocks: the shared palette, the turbo
// ramp, per-face reductions of per-vertex fields, and generic mode
// factories (mask / highlights) any plugin can reuse.

import type { FieldDescriptor } from '../api/types';
import { fetchBin } from '../fields/fields';
import type {
  ColorBar, LegendEntry, LegendFocus, PaintInfo, RGB, ViewCtx, ViewMode,
} from '../registry/types';
import {
  diverging, divergingGradientCss, sequential, sequentialGradientCss,
} from '../viewer/colormaps';

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
  chamfer: [0.85, 0.72, 0.32] as RGB,
  /** Highlight-band selection over a heatmap — magenta, the one hue the
   * batlow/viridis sequential ramps never produce. */
  band: [1.0, 0.15, 0.65] as RGB,
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

// Scalar magnitude ramp — now the perceptually-uniform, CVD-safe sequential
// map (batlow), background-aware. Kept named `rampColor` so every existing
// heatmap (thickness, gap, fill time, …) picks it up with no other change.
export function rampColor(t: number): RGB {
  return sequential(t);
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
    faces: number[];
  }>();

  constructor(private ctx: ViewCtx) {}

  add(key: string, f: number) {
    let g = this.groups.get(key);
    if (!g) {
      g = {
        n: 0, nx: 0, ny: 0, nz: 0,
        min: [Infinity, Infinity, Infinity],
        max: [-Infinity, -Infinity, -Infinity],
        faces: [],
      };
      this.groups.set(key, g);
    }
    const { verts, faces, normals } = this.ctx;
    g.n++;
    g.faces.push(f);
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
      faces: g.faces,
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
    const faces: number[] = [];
    for (const key of keys) {
      const g = this.groups.get(key);
      if (!g) continue;
      n += g.n;
      normal[0] += g.nx;
      normal[1] += g.ny;
      normal[2] += g.nz;
      faces.push(...g.faces);
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
        faces,
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
  thresholdParam?: string; // ctx.params key — drawn as a limit tick + flag count
  scaleParam?: string; // ctx.params key: manual MAX override (blank = data max)
  minParam?: string; // ctx.params key: manual MIN override (blank = data min)
  /** 'above': high values are bad. 'below': low values are bad (default). Only
      affects the flag count in the stats line. */
  flagDirection?: 'above' | 'below';
  units?: string;
  /** Signed field with a meaningful zero → diverging map, symmetric about 0
      (0 sits at the centre of the scale and the legend). */
  diverging?: boolean;
  /** Per-VERTEX mask (1 = reading explainable by local sharp geometry). These
      faces are kept out of the data range (so artifacts don't stretch the
      scale) and, when maskParam is on, painted neutral. */
  exclusion?: (ctx: ViewCtx) => Promise<Uint8Array | null>;
  /** ctx.params key of a boolean: when true, edge-explained faces are hidden. */
  maskParam?: string;
  maskedLabel?: string;
  /** ctx.params keys of the HIGHLIGHT band bounds: the heatmap stays
   * unchanged, faces whose value falls inside [lo, hi] (a missing bound is
   * open-ended) are painted COL.band on top of it. */
  bandLoParam?: string;
  bandHiParam?: string;
  // retired but kept for call-site compatibility (no longer used):
  autoFloor?: number;
  okLabel?: string;
}

/**
 * Generic per-vertex scalar heatmap. The colour scale spans the ACTUAL data
 * range by default (min→max for sequential, symmetric ±M with 0 centred for
 * diverging) so nothing is clipped, and it publishes a `colorbar` the legend
 * renders with real min/max (and a 0 marker + limit tick). A manual min/max
 * override is available via scaleParam/minParam.
 *
 * (The CNC gap/stickout modes predate this factory and keep their own
 * angle-dependent thresholds — folding them on is a possible follow-up.)
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
    async paint(ctx): Promise<PaintInfo> {
      const desc = pickField(ctx);
      if (!desc) {
        throw new Error(`no cached field for "${label}" — run the analysis first`);
      }
      const raw = await ctx.getField(desc) as Float32Array;
      const field = opts.transform ? Float32Array.from(raw, opts.transform) : raw;
      const vals = faceValues(ctx, field, null);
      const excluded = opts.exclusion ? await opts.exclusion(ctx) : null;
      const units = opts.units ?? 'mm';
      const thr = parse(ctx.params[opts.thresholdParam ?? 'threshold']);
      const maskOn = opts.maskParam ? ctx.params[opts.maskParam] !== false : false;

      // one excluded vertex vetoes the face (the CLI's all-verts rule)
      const faceExcluded = (f: number) => !!excluded
        && (!!excluded[ctx.faces[3 * f]]
          || !!excluded[ctx.faces[3 * f + 1]]
          || !!excluded[ctx.faces[3 * f + 2]]);

      // data range from finite, non-excluded faces so edge artifacts don't
      // stretch the scale and wash out the real variation
      let lo = Infinity;
      let hi = -Infinity;
      for (let f = 0; f < ctx.faceCount; f++) {
        const v = vals[f];
        if (!isFinite(v) || faceExcluded(f)) continue;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
      if (lo > hi) { lo = 0; hi = 1; } // nothing to show
      const mMin = parse(ctx.params[opts.minParam ?? '']);
      const mMax = parse(ctx.params[opts.scaleParam ?? 'scale']);
      if (isFinite(mMin)) lo = mMin;
      if (isFinite(mMax)) hi = mMax;

      let domainMin: number;
      let domainMax: number;
      let colorAt: (v: number) => RGB;
      let gradient: string;
      if (opts.diverging) {
        const M = Math.max(Math.abs(lo), Math.abs(hi), 1e-9);
        domainMin = -M;
        domainMax = M;
        colorAt = (v) => diverging(v / M);
        gradient = divergingGradientCss();
      } else {
        domainMin = lo;
        domainMax = hi;
        const span = Math.max(hi - lo, 1e-9);
        colorAt = (v) => sequential((v - lo) / span);
        gradient = sequentialGradientCss();
      }

      // highlight band: selection painted OVER the unchanged heatmap
      const bandLo = parse(ctx.params[opts.bandLoParam ?? '']);
      const bandHi = parse(ctx.params[opts.bandHiParam ?? '']);
      const bandActive = isFinite(bandLo) || isFinite(bandHi);
      const bLo = isFinite(bandLo) ? bandLo : -Infinity;
      const bHi = isFinite(bandHi) ? bandHi : Infinity;

      const below = opts.flagDirection !== 'above';
      let flagged = 0;
      let painted = 0;
      let inBand = 0;
      // findings for the viewport's "findings only" filter: the highlight
      // band when one is set, else the threshold-flagged faces
      const finding = new Uint8Array(ctx.faceCount);
      ctx.paintFaces((f) => {
        const v = vals[f];
        if (!isFinite(v)) return COL.inaccess;
        painted++;
        const isExcl = faceExcluded(f);
        if (isFinite(thr) && !isExcl && (below ? v <= thr : v >= thr)) {
          flagged++;
          if (!bandActive) finding[f] = 1;
        }
        if (isExcl && maskOn) return COL.ok; // hide known edge artifacts
        if (bandActive && v >= bLo && v <= bHi) {
          inBand++;
          finding[f] = 1;
          return COL.band;
        }
        return colorAt(v);
      });
      if (bandActive || isFinite(thr)) ctx.setFindings((f) => !!finding[f]);

      const colorbar: ColorBar = {
        min: domainMin,
        max: domainMax,
        unit: units,
        diverging: !!opts.diverging,
        gradient,
        threshold: isFinite(thr) ? thr : undefined,
      };
      const bandTxt = bandActive
        ? (isFinite(bandLo) && isFinite(bandHi)
          ? `${bandLo.toFixed(2)} – ${bandHi.toFixed(2)} ${units}`
          : isFinite(bandLo) ? `≥ ${bandLo.toFixed(2)} ${units}`
          : `≤ ${bandHi.toFixed(2)} ${units}`)
        : '';
      // minimal discrete entries for the original viewer + as a fallback
      const legend: LegendEntry[] = [
        ...(bandActive
          ? [{ color: COL.band, label: `in band ${bandTxt}` }] : []),
        { color: colorAt(domainMin), label: `${domainMin.toFixed(2)} ${units}` },
        { color: colorAt(domainMax), label: `${domainMax.toFixed(2)} ${units}` },
        { color: COL.inaccess, label: opts.maskedLabel ?? 'no data' },
      ];
      const rangeTxt = `range ${domainMin.toFixed(2)} – ${domainMax.toFixed(2)} ${units}`;
      const parts = [
        bandActive ? `${inBand} of ${painted} faces in band ${bandTxt}` : '',
        isFinite(thr)
          ? `${flagged} of ${painted} faces ${below ? 'below' : 'above'} ${thr} ${units}`
          : '',
        rangeTxt,
      ].filter(Boolean);
      const stats = parts.join(' · ');
      return { legend, stats, colorbar };
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

/** Per-triangle source BREP face ids for whichever mesh is currently displayed.
 * Prefers the fine `subfaces`/`brep_faces` field; falls back to the coarse
 * preview's `coarse_brep_faces` array when only the coarse mesh is loaded (the
 * fine mesh + its `brep_faces` field are computed on demand). Face ids are
 * stable across subdivision, so the coarse ids color the preview identically —
 * and `paintFaces` iterates the displayed mesh, so the array aligns 1:1. */
async function loadBrepFaceIds(
  ctx: ViewCtx,
): Promise<{ ids: Uint32Array; desc: FieldDescriptor | null }> {
  const desc = ctx.manifest.fields.find((f) => f.id === 'subfaces')
    ?? ctx.manifest.fields.find((f) => f.id === 'brep_faces')
    ?? null;
  if (desc) return { ids: await ctx.getField(desc) as Uint32Array, desc };
  const coarseUrl = ctx.manifest.coarse_mesh?.brep_faces_url;
  if (coarseUrl) return { ids: await fetchBin(coarseUrl, Uint32Array), desc: null };
  throw new Error('no BREP face ids — re-mesh the part from its STEP file');
}

/** Source BREP faces (from the STEP-aware mesher), one color per face id.
 * User face splits show as their sub-face pieces when present. */
export const brepFacesMode: ViewMode = {
  id: 'brep_faces',
  label: 'BREP faces',
  async paint(ctx) {
    const { ids, desc } = await loadBrepFaceIds(ctx);
    ctx.paintFaces((f) => segmentIdColor(ids[f]));
    if (!desc) {
      return { legend: [], stats: `BREP faces (coarse preview) over ${ctx.faceCount} triangles` };
    }
    const split = desc.params.n_brep != null
      ? ` (${desc.params.n_brep} BREP + user cuts)` : '';
    return {
      legend: [],
      stats: `${desc.params.count} faces${split} over ${ctx.faceCount} triangles`,
    };
  },
};

/** STEP-assigned face colors and names (face_attrs.json from the import
 * front-end), painted per source BREP face. Named / PMI-annotated faces get
 * click-to-fly legend entries. */
export const faceAttrsMode: ViewMode = {
  id: 'face_attrs',
  label: 'STEP colors / names',
  async paint(ctx) {
    const url = ctx.manifest.face_attrs_url;
    if (!url) {
      throw new Error('no STEP face attributes — import the part from a STEP file that carries colors/names (explode command or upload)');
    }
    const attrs = await (await fetch(url)).json();
    const { ids } = await loadBrepFaceIds(ctx);

    const byId = new Map<number, { color: RGB | null; name: string | null; pmi_refs: number[] }>();
    for (const [key, value] of Object.entries(attrs.faces ?? {})) {
      byId.set(parseInt(key, 10), value as any);
    }
    const base: RGB = attrs.part_color ?? COL.ok;
    const tracker = new FocusTracker(ctx);
    ctx.paintFaces((f) => {
      const entry = byId.get(ids[f]);
      if (entry?.name) tracker.add(`name:${ids[f]}`, f);
      if (entry?.pmi_refs?.length) tracker.add(`pmi:${ids[f]}`, f);
      return (entry?.color as RGB) ?? base;
    });

    let colored = 0;
    let named = 0;
    let annotated = 0;
    const legend: LegendEntry[] = [];
    for (const [id, entry] of byId) {
      if (entry.color) colored++;
      if (entry.pmi_refs?.length) annotated++;
      if (entry.name) {
        named++;
        if (legend.length < 10) {
          legend.push({
            color: (entry.color as RGB) ?? base,
            label: `“${entry.name}”`,
            focus: tracker.focus(`name:${id}`),
          });
        }
      }
    }
    if (annotated) {
      legend.push({
        color: COL.side,
        label: `${annotated} PMI-annotated faces`,
        focus: tracker.merged(tracker.keys('pmi:')),
      });
    }
    if (attrs.part_color) legend.push({ color: base, label: 'part color' });
    return {
      legend,
      stats: `${colored} colored, ${named} named, ${annotated} PMI-annotated BREP faces`,
    };
  },
};

/** PMI / GD&T view: paints exactly the BREP faces the PmiRail selection asks
 * for, each in a caller-chosen colour (toleranced amber, dimensions blue, and
 * one distinct hue per datum). The rail pushes a full [brepFaceId, RGB] colour
 * map plus a matching legend through `viewerParams`; every other face is left
 * unpainted (null) so it keeps the native viewport style. */
// PMI reuses the same golden-ratio segmentation palette as the BREP-faces lens
// (segmentIdColor). Slots 0 and 1 are reserved for toleranced / dimensioned
// features; datums take slots 2+ (datumColors.ts), so a control-frame's
// referenced features never share a colour with a datum.
export const PMI_ANNO_COL: RGB = segmentIdColor(0);   // toleranced faces
export const PMI_DIM_COL: RGB = segmentIdColor(1);    // dimensioned faces

export const pmiMode: ViewMode = {
  id: 'pmi',
  label: 'PMI / GD&T',
  async paint(ctx) {
    const { ids } = await loadBrepFaceIds(ctx);
    const colorMap = new Map<number, RGB>(
      (ctx.params.pmiColorMap ?? []) as Array<[number, RGB]>);
    ctx.paintFaces((f) => colorMap.get(ids[f]) ?? null);
    // the coloured faces are the findings (stay full); the rest is native shell
    ctx.setFindings((f) => colorMap.has(ids[f]));
    const legend = ((ctx.params.pmiLegend ?? []) as Array<{ color: RGB; label: string }>)
      .map((e) => ({ color: e.color, label: e.label }));
    const counts = ctx.params.pmiCounts as
      { tolerances: number; dimensions: number; datums: number } | undefined;
    const stats = counts
      ? `${counts.tolerances} tolerances · ${counts.dimensions} dimensions · ${counts.datums} datums`
      : 'Select a PMI entry in the panel to highlight its faces.';
    return { legend, stats };
  },
};

/** "Last CLI highlights.json" — process-agnostic replay of the legacy result. */
export const highlightsMode: ViewMode = {
  id: 'highlights',
  label: 'Last CLI highlights.json',
  async paint(ctx) {
    if (!ctx.highlights) throw new Error('no highlights.json in the working directory');
    const flagged = new Set(ctx.highlights);
    const tracker = new FocusTracker(ctx); // legend click -> fly to the group
    ctx.paintFaces((f) => {
      if (flagged.has(f)) { tracker.add('flagged', f); return COL.tip; }
      return null; // un-flagged faces keep the native viewport style
    });
    ctx.setFindings((f) => flagged.has(f));
    return {
      legend: [
        { color: COL.tip, label: `flagged by last CLI run (${ctx.highlights.length} faces)`, focus: tracker.focus('flagged') },
      ],
    };
  },
};
