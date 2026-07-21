// Reach-study lenses: slice the stored cnc/reach_study result (per
// (direction, tool) face masks) into single-pair, per-operation and
// cross-operation views. Pure mask logic over cached fields — no server
// round-trips beyond field fetches (docs/PLAN-ARCHITECTURE.md, Phase 2).

import { COL, FocusTracker } from '../../colorizers/core';
import type { FieldDescriptor, Manifest, ResultEntry } from '../../api/types';
import type { PaintInfo, ViewCtx, ViewMode } from '../../registry/types';

export const REACH_SCHEMA = 1; // mirror processes/cnc.py REACH_STUDY_SCHEMA

/** The slice of ViewCtx the data helpers need — ViewCtx satisfies it, and
 * so does a plain object built from the manifest + field fetcher, which is
 * how the v2 check evaluators reuse these helpers outside the viewer. */
export interface ReachCtx {
  manifest: Manifest;
  directions: number[][];
  faceCount: number;
  params: Record<string, any>;
  getField(desc: FieldDescriptor): Promise<Float32Array | Uint8Array | Uint32Array>;
}

export interface ReachStudy {
  entry: ResultEntry;
  hash: string;
  /** Global direction indices the study covers. */
  directions: number[];
  tools: { diameter: number; corner_radius: number;
    stickout: number | null; holder_radius: number | null }[];
}

/** The reach study to slice: `params.reachHash` pins one (check binding);
 * otherwise the latest stored cnc/reach_study result. */
export function findStudy(ctx: ReachCtx): ReachStudy {
  const all = ctx.manifest.results.filter(
    (r) => r.process === 'cnc' && r.analysis === 'reach_study');
  const wanted = ctx.params.reachHash;
  const entry = wanted
    ? all.find((r) => r.hash === wanted)
    : all[all.length - 1];
  if (!entry) {
    throw new Error(wanted
      ? 'the plan\'s reach study is not computed yet — run its check first'
      : 'no reach study yet — run cnc/reach_study in the Compute panel');
  }
  return {
    entry,
    hash: entry.hash,
    directions: (entry.stats.directions ?? []) as number[],
    tools: (entry.stats.tools ?? []) as ReachStudy['tools'],
  };
}

async function fetchMask(
  ctx: ReachCtx, study: ReachStudy, d: number, t: number,
): Promise<Uint8Array> {
  const id = `results.cnc.reach_study.${study.hash}.reach_${d}_${t}`;
  const desc = ctx.manifest.fields.find((f) => f.id === id);
  if (!desc) throw new Error(`study has no mask for direction ${d} tool ${t}`);
  return await ctx.getField(desc) as Uint8Array;
}

async function fetchAccess(ctx: ReachCtx, d: number): Promise<Uint8Array | null> {
  const desc = ctx.manifest.fields.find((f) => f.id === `accessibility.${d}`);
  return desc ? (await ctx.getField(desc)) as Uint8Array : null;
}

/** Sampled direction indices within `tiltDeg` of the primary — the TS
 * mirror of machining.cone_members (3-axis: tilt 0 → just the primary). */
export function coneMembers(
  directions: number[][], primary: number, tiltDeg: number,
): number[] {
  const p = directions[primary];
  if (!p) return [];
  const minDot = Math.cos((tiltDeg * Math.PI) / 180) - 1e-9;
  const members: number[] = [];
  for (let i = 0; i < directions.length; i++) {
    const d = directions[i];
    if (d[0] * p[0] + d[1] * p[1] + d[2] * p[2] >= minDot) members.push(i);
  }
  return members;
}

/** Per-face triangle areas (mm²), cached per mesh. */
let areaCache: { key: string; areas: Float64Array } | null = null;
export function faceAreas(ctx: ViewCtx): Float64Array {
  const key = `${ctx.faceCount}:${ctx.verts.length}`;
  if (areaCache?.key === key) return areaCache.areas;
  const areas = new Float64Array(ctx.faceCount);
  const { verts, faces } = ctx;
  for (let f = 0; f < ctx.faceCount; f++) {
    const a = 3 * faces[3 * f], b = 3 * faces[3 * f + 1], c = 3 * faces[3 * f + 2];
    const ux = verts[b] - verts[a], uy = verts[b + 1] - verts[a + 1], uz = verts[b + 2] - verts[a + 2];
    const vx = verts[c] - verts[a], vy = verts[c + 1] - verts[a + 1], vz = verts[c + 2] - verts[a + 2];
    const cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx;
    areas[f] = 0.5 * Math.sqrt(cx * cx + cy * cy + cz * cz);
  }
  areaCache = { key, areas };
  return areas;
}

const toolLabel = (t: ReachStudy['tools'][number], i: number) =>
  `T${i + 1} D${t.diameter}${t.corner_radius ? `:r${t.corner_radius}` : ''}`;

/** Union of reach masks over (cone members ∩ study directions) × all tools,
 * plus the union of accessibility over the same members. */
export async function opReach(
  ctx: ReachCtx, study: ReachStudy, primary: number, tiltDeg: number,
): Promise<{ reach: Uint8Array; visible: Uint8Array; members: number[] }> {
  const members = coneMembers(ctx.directions, primary, tiltDeg)
    .filter((d) => study.directions.includes(d));
  if (!members.length) {
    throw new Error(`the study covers none of the directions in the `
      + `operation's ±${tiltDeg}° cone — extend the study's direction list`);
  }
  const reach = new Uint8Array(ctx.faceCount);
  const visible = new Uint8Array(ctx.faceCount);
  for (const d of members) {
    for (let t = 0; t < study.tools.length; t++) {
      const mask = await fetchMask(ctx, study, d, t);
      for (let f = 0; f < ctx.faceCount; f++) reach[f] |= mask[f];
    }
    const access = await fetchAccess(ctx, d);
    if (access) for (let f = 0; f < ctx.faceCount; f++) visible[f] |= access[f];
  }
  return { reach, visible, members };
}

const pct = (part: number, whole: number) =>
  whole > 0 ? `${((100 * part) / whole).toFixed(1)}%` : '—';

/** Latest non-stale cnc/features feature_id field (0 = not a feature) —
 * the declarative "this operation produces these features" scoping. */
export async function fetchFeatureMask(
  ctx: ReachCtx,
): Promise<Uint32Array | null> {
  const list = ctx.manifest.results.filter(
    (r) => r.process === 'cnc' && r.analysis === 'features' && !r.stale);
  const result = list[list.length - 1];
  if (!result) return null;
  const desc = ctx.manifest.fields.find(
    (f) => f.id === `results.cnc.features.${result.hash}.feature_id`);
  return desc ? await ctx.getField(desc) as Uint32Array : null;
}

export const reachStudyMode: ViewMode = {
  id: 'reach_study',
  label: 'Reach study (direction × tool)',
  async paint(ctx): Promise<PaintInfo> {
    const study = findStudy(ctx);
    const d = Number.isFinite(parseInt(ctx.params.reachDirection, 10))
      ? parseInt(ctx.params.reachDirection, 10) : study.directions[0];
    const t = Math.min(Math.max(parseInt(ctx.params.reachTool, 10) || 0, 0),
      study.tools.length - 1);
    if (!study.directions.includes(d)) {
      throw new Error(`the study does not cover direction ${d} `
        + `(covered: ${study.directions.join(', ')})`);
    }
    const mask = await fetchMask(ctx, study, d, t);
    const access = await fetchAccess(ctx, d);
    const areas = faceAreas(ctx);
    const tracker = new FocusTracker(ctx);
    let ok = 0, blocked = 0, blockedArea = 0;
    ctx.paintFaces((f) => {
      if (mask[f]) { ok++; tracker.add('ok', f); return COL.ok; }
      if (access && !access[f]) { tracker.add('inaccess', f); return COL.inaccess; }
      blocked++; blockedArea += areas[f];
      tracker.add('blocked', f); return COL.tip;
    });
    return {
      legend: [
        { color: COL.ok, label: `reachable — ${toolLabel(study.tools[t], t)}`, focus: tracker.focus('ok') },
        { color: COL.tip, label: 'accessible but tool-blocked', focus: tracker.focus('blocked') },
        { color: COL.inaccess, label: 'undercut for this direction', focus: tracker.focus('inaccess') },
      ],
      stats: `direction ${d} · ${toolLabel(study.tools[t], t)} · `
        + `${ok} reachable · ${blocked} blocked (${blockedArea.toFixed(0)} mm²)`,
    };
  },
};

export const reachOpMode: ViewMode = {
  id: 'reach_op',
  label: 'Operation reach (any tool in cone)',
  async paint(ctx): Promise<PaintInfo> {
    const study = findStudy(ctx);
    const primary = parseInt(ctx.params.opPrimary, 10);
    if (!Number.isFinite(primary)) {
      throw new Error('pick the operation\'s primary direction (opPrimary)');
    }
    const tilt = parseFloat(ctx.params.opTilt) || 0;
    const { reach, visible, members } = await opReach(ctx, study, primary, tilt);
    // features scoping: only the faces this operation PRODUCES are judged;
    // the rest of the part renders as neutral context
    const featureMask = ctx.params.reachFeatureMask
      ? await fetchFeatureMask(ctx) : null;
    const areas = faceAreas(ctx);
    const tracker = new FocusTracker(ctx);
    let ok = 0, blocked = 0, blockedArea = 0;
    ctx.paintFaces((f) => {
      if (featureMask && !featureMask[f]) return COL.below;
      if (reach[f]) { ok++; tracker.add('ok', f); return COL.ok; }
      if (!visible[f]) { tracker.add('inaccess', f); return COL.inaccess; }
      blocked++; blockedArea += areas[f];
      tracker.add('blocked', f); return COL.tip;
    });
    const scopeTxt = featureMask ? 'machined-feature faces' : 'faces';
    return {
      legend: [
        ...(featureMask
          ? [{ color: COL.below, label: 'not a machined feature (context)' }]
          : []),
        { color: COL.ok, label: 'reachable in this operation', focus: tracker.focus('ok') },
        { color: COL.tip, label: 'visible but no tool reaches', focus: tracker.focus('blocked') },
        { color: COL.inaccess, label: 'undercut for the whole cone', focus: tracker.focus('inaccess') },
      ],
      stats: `direction ${primary} ±${tilt}° (${members.length} sampled) · `
        + `${study.tools.length} tools · ${ok} ${scopeTxt} reachable `
        + `· blocked ${blocked} (${blockedArea.toFixed(0)} mm²)`,
    };
  },
};

export const reachAggregateMode: ViewMode = {
  id: 'reach_aggregate',
  label: 'Route reach (all operations)',
  async paint(ctx): Promise<PaintInfo> {
    const study = findStudy(ctx);
    const ops = (ctx.params.reachOps ?? []) as { primary: number; tilt: number; label?: string }[];
    if (!ops.length) {
      throw new Error('no operations configured — add CNC operations to the '
        + 'plan (each contributes its direction cone)');
    }
    const anyReach = new Uint8Array(ctx.faceCount);
    const anyVisible = new Uint8Array(ctx.faceCount);
    for (const op of ops) {
      const { reach, visible } = await opReach(ctx, study, op.primary, op.tilt);
      for (let f = 0; f < ctx.faceCount; f++) {
        anyReach[f] |= reach[f];
        anyVisible[f] |= visible[f];
      }
    }
    const areas = faceAreas(ctx);
    const tracker = new FocusTracker(ctx);
    let ok = 0, blocked = 0, blockedArea = 0, hidden = 0;
    ctx.paintFaces((f) => {
      if (anyReach[f]) { ok++; tracker.add('ok', f); return COL.ok; }
      if (!anyVisible[f]) { hidden++; tracker.add('inaccess', f); return COL.inaccess; }
      blocked++; blockedArea += areas[f];
      tracker.add('blocked', f); return COL.tip;
    });
    return {
      legend: [
        { color: COL.ok, label: 'producible by the route', focus: tracker.focus('ok') },
        { color: COL.tip, label: 'unreachable in every operation', focus: tracker.focus('blocked') },
        { color: COL.inaccess, label: 'undercut for every operation', focus: tracker.focus('inaccess') },
      ],
      stats: `${ops.length} operations · ${ok} of ${ctx.faceCount} faces `
        + `producible (${pct(ok, ctx.faceCount)}) · unreachable ${blocked + hidden} `
        + `(${blockedArea.toFixed(0)} mm² tool-blocked)`,
    };
  },
};
