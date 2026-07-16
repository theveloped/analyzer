// Shared client-side parting-line optimizer for the two categorical
// assignment views (CNC "Setup assignment", injection "Mold orientation
// assignment"). Both let the user click striped multi-valid BREP faces to
// cycle which feature owns them, moving the drawn parting line. This module
// reassigns every multi-valid face at once so the initial view minimizes,
// lexicographically, (1) the number of distinct parting-line wires, then
// (2) total parting-line length — writing the result through the existing
// per-BREP-face overrides mechanism (no backend / recompute).

import { putOverrides } from '../api/client';
import type { ViewCtx } from '../registry/types';
import { useStore } from '../state/store';
import { edgeDescriptors } from '../splits/splits';

// Labels >= EXCLUDED are the 254 (conflict) / 255 (internal) sentinels; a
// segment between them never draws a parting line.
const EXCLUDED = 254;

/** Per-BREP-face assignment inputs — satisfied as-is by both SetupsData and
 * AssignmentData. */
export interface PartingData {
  valid: Uint32Array;      // brep_valid_<option>, per BREP face id (bitmask)
  defaults: Uint8Array;    // brep_default_<option>
  current: Uint8Array;     // defaults + applied overrides
  option: number;          // field-option index
  overridesKey: string;    // `${process}.${analysis}.${hash}`
  overridesUrl?: string;   // result.overrides_url
}

/** Both parting-line objectives over one labeling, the single source of
 * truth for the search and the before/after report. `wires` welds active
 * segment endpoints (exact copied mesh-vertex coords) into a union-find and
 * counts connected components; `length` sums active segment lengths. A
 * segment is active iff its two faces carry distinct, non-sentinel labels. */
export function partingMetrics(
  segments: Float32Array, pairs: Uint32Array, label: Uint8Array,
): { wires: number; length: number } {
  const E = pairs.length / 2;
  let length = 0;
  const nodeId = new Map<string, number>();
  const parent: number[] = [];
  const nodeOf = (x: number, y: number, z: number): number => {
    const key = `${Math.round(x * 1e4)}|${Math.round(y * 1e4)}|${Math.round(z * 1e4)}`;
    let id = nodeId.get(key);
    if (id === undefined) {
      id = parent.length;
      nodeId.set(key, id);
      parent.push(id);
    }
    return id;
  };
  const find = (i: number): number => {
    let r = i;
    while (parent[r] !== r) r = parent[r];
    while (parent[i] !== r) { const n = parent[i]; parent[i] = r; i = n; }
    return r;
  };
  for (let e = 0; e < E; e++) {
    // ids past the label array = sub-faces newer than the result (interim
    // between a face split and its re-run) — excluded like the sentinels
    const pa = pairs[2 * e];
    const pb = pairs[2 * e + 1];
    const la = pa < label.length ? label[pa] : EXCLUDED;
    const lb = pb < label.length ? label[pb] : EXCLUDED;
    if (la >= EXCLUDED || lb >= EXCLUDED || la === lb) continue;
    const o = 6 * e;
    const x0 = segments[o], y0 = segments[o + 1], z0 = segments[o + 2];
    const x1 = segments[o + 3], y1 = segments[o + 4], z1 = segments[o + 5];
    length += Math.hypot(x1 - x0, y1 - y0, z1 - z0);
    const na = nodeOf(x0, y0, z0);
    const nb = nodeOf(x1, y1, z1);
    const ra = find(na), rb = find(nb);
    if (ra !== rb) parent[ra] = rb;
  }
  let wires = 0;
  for (let i = 0; i < parent.length; i++) if (find(i) === i) wires++;
  return { wires, length };
}

/** Small, reproducible PRNG so repeated clicks give the same result. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export async function optimizeParting(
  ctx: ViewCtx, data: PartingData,
): Promise<{ summary: string; changed: boolean }> {
  // 1. Fetch parting-line geometry — the effective sub-face boundary
  // arrays when user face splits exist, plain BREP edges otherwise, so the
  // optimizer moves the same segments the views draw (incl. cut edges).
  const lineDescs = edgeDescriptors(ctx.manifest);
  if (!lineDescs) {
    return { summary: 'optimize needs BREP edges — re-mesh from STEP', changed: false };
  }
  const segments = await ctx.getField(lineDescs.edges) as Float32Array;
  const pairs = await ctx.getField(lineDescs.pairs) as Uint32Array;

  // 2. Sets & domains. A face contributes iff valid != 0; it is movable iff
  // its valid mask has >= 2 bits.
  const N = data.valid.length;
  const movable: number[] = [];
  const domain: number[][] = new Array(N);
  for (let b = 0; b < N; b++) {
    const v = data.valid[b];
    if (v !== 0 && (v & (v - 1)) !== 0) {
      const bits: number[] = [];
      for (let f = 0; f < 32; f++) if ((v >>> f) & 1) bits.push(f);
      domain[b] = bits;
      movable.push(b);
    }
  }
  if (movable.length === 0) {
    return { summary: 'no multi-valid faces — nothing to optimize', changed: false };
  }

  // 3. Weighted adjacency over contributing faces (weight = shared boundary
  // length). Backend sorts each pair a < b.
  const pairW = new Map<number, number>();
  for (let e = 0; e < pairs.length / 2; e++) {
    const a = pairs[2 * e];
    const b = pairs[2 * e + 1];
    // ids >= N are sub-faces the stale result doesn't know yet — skip
    if (a >= N || b >= N || data.valid[a] === 0 || data.valid[b] === 0) continue;
    const o = 6 * e;
    const len = Math.hypot(
      segments[o + 3] - segments[o], segments[o + 4] - segments[o + 1],
      segments[o + 5] - segments[o + 2]);
    const k = a * N + b;
    pairW.set(k, (pairW.get(k) ?? 0) + len);
  }
  const adj: [number, number][][] = Array.from({ length: N }, () => []);
  for (const [k, w] of pairW) {
    const a = Math.floor(k / N);
    const b = k % N;
    adj[a].push([b, w]);
    adj[b].push([a, w]);
  }

  // 4. Coordinate-descent (ICM) from a seed -> local length minimum.
  const icm = (seed: Uint8Array): Uint8Array => {
    const assign = new Uint8Array(data.current); // fixed faces keep their label
    for (const b of movable) assign[b] = seed[b];
    for (let sweep = 0; sweep < 20; sweep++) {
      let changed = false;
      for (const b of movable) {
        const dom = domain[b];
        const nb = adj[b];
        const incumbent = assign[b];
        let bestD = dom[0];
        let bestCost = Infinity;
        let incCost = Infinity;
        for (const d of dom) {
          let cost = 0;
          for (const [c, w] of nb) if (assign[c] !== d) cost += w;
          if (d === incumbent) incCost = cost;
          if (cost < bestCost) { bestCost = cost; bestD = d; }
        }
        const chosen = incCost === bestCost ? incumbent : bestD;
        if (chosen !== incumbent) { assign[b] = chosen; changed = true; }
      }
      if (!changed) break;
    }
    return assign;
  };

  // 5. Seeds & restarts.
  let maxBit = 0;
  for (let b = 0; b < N; b++) {
    const v = data.valid[b];
    if (v) { const hb = 31 - Math.clz32(v); if (hb > maxBit) maxBit = hb; }
  }
  const candidates: Uint8Array[] = [icm(data.current), icm(data.defaults)];
  for (let f = 0; f <= Math.min(maxBit, 7); f++) {
    const seed = new Uint8Array(N);
    for (const b of movable) {
      seed[b] = ((data.valid[b] >>> f) & 1) ? f : domain[b][0];
    }
    candidates.push(icm(seed));
  }
  const rnd = mulberry32(0x6d2b79f5);
  for (let r = 0; r < 32; r++) {
    const seed = new Uint8Array(N);
    for (const b of movable) {
      const dom = domain[b];
      seed[b] = dom[Math.floor(rnd() * dom.length)];
    }
    candidates.push(icm(seed));
  }

  // 6. Select by lexicographic (wires, length). Raw current/defaults are in
  // the pool so the pick is never worse than the shown state.
  const pool: Uint8Array[] = [...candidates, data.current, data.defaults];
  let best = pool[0];
  let bestM = partingMetrics(segments, pairs, best);
  for (let i = 1; i < pool.length; i++) {
    const m = partingMetrics(segments, pairs, pool[i]);
    if (m.wires < bestM.wires
        || (m.wires === bestM.wires && m.length < bestM.length)) {
      best = pool[i];
      bestM = m;
    }
  }

  // 7. Apply & persist through the overrides mechanism.
  const before = partingMetrics(segments, pairs, data.current);
  const after = partingMetrics(segments, pairs, best);
  let changed = false;
  const { setOverride } = useStore.getState();
  for (const b of movable) {
    const v = best[b];
    if (v !== data.current[b]) changed = true;
    setOverride(data.overridesKey, data.option, b,
                v === data.defaults[b] ? null : v);
  }
  const payload = useStore.getState().overrides[data.overridesKey] ?? {};
  if (data.overridesUrl) {
    putOverrides(data.overridesUrl, payload).catch((err) =>
      useStore.getState().set({ error: String(err) }));
  }

  // 8. Summary.
  const fmt = (m: { wires: number; length: number }) =>
    `${m.wires} wire${m.wires === 1 ? '' : 's'} · ${m.length.toFixed(0)} mm`;
  return changed
    ? { summary: `parting optimized: ${fmt(before)} → ${fmt(after)}`, changed: true }
    : { summary: `parting already optimal: ${fmt(before)}`, changed: false };
}
