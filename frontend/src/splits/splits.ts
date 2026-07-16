// User-driven BREP face splitting, shared by the mold-assignment and CNC
// setups views. A cut is two clicks on a face's boundary wires — snapped
// to wire corners (BREP edge endpoints) or edge midpoints — committed to
// POST /api/parts/{id}/splits. The backend relabels the face's triangles
// into sub-face ids (no remeshing) and regenerates the effective boundary
// arrays; the affected assignment analysis is then re-run automatically so
// the pieces classify individually.

import {
  clearSplits, deleteLastSplit, fetchSplits, postSplit, putOverrides,
  type SplitsState,
} from '../api/client';
import type { FieldDescriptor, Manifest, ResultEntry } from '../api/types';
import type { RGB, ViewCtx } from '../registry/types';
import { useStore } from '../state/store';
import { refreshManifest } from '../viewer/controller';
import { runAnalysisJob } from '../viewer/jobs';

export const CUT_COLOR: RGB = [0.98, 0.98, 0.98];
const CORNER_COLOR: RGB = [0.92, 0.94, 0.97];
const MIDPOINT_COLOR: RGB = [1.0, 0.78, 0.2];
const ARMED_COLOR: RGB = [0.35, 0.95, 0.45];

/** How each process wires the split interaction to its assignment result. */
export interface SplitHost {
  processId: string;
  /** Assignment view-mode id the split toggle applies to. */
  modeId: string;
  /** Currently selected assignment result (to re-run after a cut). */
  currentResult(manifest: Manifest, params: Record<string, any>): ResultEntry | undefined;
  /** Job analysis id that recomputes a given stored result. */
  analysisOf(result: ResultEntry): string;
  /** Result-selector param to reset to "latest" after the re-run. */
  resultParam: string;
}

/** Per-triangle face labeling: user sub-faces when splits exist. */
export function effectiveDescriptor(manifest: Manifest): FieldDescriptor | undefined {
  return manifest.fields.find((f) => f.id === 'subfaces')
    ?? manifest.fields.find((f) => f.id === 'brep_faces');
}

/** Boundary segment arrays matching the effective labeling. */
export function edgeDescriptors(manifest: Manifest):
{ edges: FieldDescriptor; pairs: FieldDescriptor } | null {
  const pick = (a: string, b: string) => {
    const edges = manifest.fields.find((f) => f.id === a);
    const pairs = manifest.fields.find((f) => f.id === b);
    return edges && pairs ? { edges, pairs } : null;
  };
  return pick('subface_edges', 'subface_edge_pairs')
    ?? pick('brep_edges', 'brep_edge_pairs');
}

/** "12" for original faces, "12.3" for the 3rd piece cut from face 12.
 * Piece ranks count every id ever created for the parent (retired
 * siblings leave gaps), so a label never changes under further cuts. */
export function faceLabel(id: number, desc: FieldDescriptor | undefined): string {
  const nBrep: number | undefined = desc?.params.n_brep;
  const parents: number[] = desc?.params.parents ?? [];
  if (nBrep === undefined || id < nBrep) return String(id);
  const parent = parents[id - nBrep];
  if (parent === undefined) return String(id);
  let rank = 1;
  for (let j = 0; j < id - nBrep; j++) if (parents[j] === parent) rank++;
  return `${parent}.${rank}`;
}

// ---------------------------------------------------------------- state

let splitsCache: { key: string; promise: Promise<SplitsState> } | null = null;

/** Splits state, cached per part + manifest version. */
export function loadSplits(ctx: ViewCtx): Promise<SplitsState> {
  const { partId, manifestVersion } = useStore.getState();
  const key = `${partId}:${manifestVersion}`;
  if (!partId) return Promise.reject(new Error('no part selected'));
  if (splitsCache?.key !== key) {
    const promise = fetchSplits(partId);
    promise.catch(() => { splitsCache = null; });
    splitsCache = { key, promise };
  }
  void ctx;
  return splitsCache.promise;
}

function clearSplitSelection(processId: string) {
  const { setViewerParam } = useStore.getState();
  setViewerParam(processId, 'splitFace', null);
  setViewerParam(processId, 'splitStart', null);
}

// --------------------------------------------------------- snap targets

export interface SnapTarget {
  vertex: number;
  kind: 'corner' | 'midpoint';
  pos: [number, number, number];
}

const targetCache = new Map<string, SnapTarget[]>();

function edgeKey(a: number, b: number): number {
  return a < b ? a * 0x100000000 + b : b * 0x100000000 + a;
}

/**
 * Snap targets on an effective face's boundary wires: `corner` = vertices
 * where the neighboring face changes or ≥3 boundary edges meet (the BREP
 * edge endpoints), `midpoint` = the arclength-middle mesh vertex of each
 * wire span between corners. Cornerless loops (e.g. a full circle against
 * a single neighbor) get four evenly spaced midpoints so they stay
 * cuttable.
 */
export function snapTargets(
  ctx: ViewCtx, ids: Uint32Array, faceId: number,
): SnapTarget[] {
  const { partId, manifestVersion } = useStore.getState();
  const cacheKey = `${partId}:${manifestVersion}:${faceId}`;
  const cached = targetCache.get(cacheKey);
  if (cached) return cached;
  if (targetCache.size > 64) targetCache.clear();

  const { faces, verts, faceCount } = ctx;
  // census of the face's triangle edges: seen once = boundary
  const counts = new Map<number, [number, number]>();
  for (let f = 0; f < faceCount; f++) {
    if (ids[f] !== faceId) continue;
    for (let k = 0; k < 3; k++) {
      const a = faces[3 * f + k];
      const b = faces[3 * f + ((k + 1) % 3)];
      const key = edgeKey(a, b);
      const entry = counts.get(key);
      if (entry) counts.delete(key); // interior (seen twice)
      else counts.set(key, [a, b]);
    }
  }
  // neighboring effective face per boundary edge (-1 = open mesh edge)
  const neighborOf = new Map<number, number>();
  for (const key of counts.keys()) neighborOf.set(key, -1);
  for (let f = 0; f < faceCount; f++) {
    if (ids[f] === faceId) continue;
    for (let k = 0; k < 3; k++) {
      const key = edgeKey(faces[3 * f + k], faces[3 * f + ((k + 1) % 3)]);
      if (neighborOf.has(key)) neighborOf.set(key, ids[f]);
    }
  }

  // vertex -> incident boundary edges
  const incident = new Map<number, number[]>();
  for (const [key, [a, b]] of counts) {
    for (const v of [a, b]) {
      const list = incident.get(v);
      if (list) list.push(key);
      else incident.set(v, [key]);
    }
  }

  const length = (key: number) => {
    const [a, b] = counts.get(key)!;
    return Math.hypot(
      verts[3 * a] - verts[3 * b],
      verts[3 * a + 1] - verts[3 * b + 1],
      verts[3 * a + 2] - verts[3 * b + 2]);
  };
  const isCorner = (v: number) => {
    const edges = incident.get(v)!;
    if (edges.length !== 2) return true; // open end or non-manifold pinch
    return neighborOf.get(edges[0]) !== neighborOf.get(edges[1]);
  };
  const position = (v: number): [number, number, number] =>
    [verts[3 * v], verts[3 * v + 1], verts[3 * v + 2]];

  // walk each boundary loop/chain, splitting at corners into wire spans
  const targets: SnapTarget[] = [];
  const cornerSet = new Set<number>();
  const visited = new Set<number>();
  const walkFrom = (start: number, firstEdge: number) => {
    // collect the span [start..corner]: vertices + cumulative arclength
    const span: number[] = [start];
    const cumulative: number[] = [0];
    let vertex = start;
    let edge = firstEdge;
    while (!visited.has(edge)) {
      visited.add(edge);
      const [a, b] = counts.get(edge)!;
      vertex = vertex === a ? b : a;
      cumulative.push(cumulative[cumulative.length - 1] + length(edge));
      span.push(vertex);
      if (isCorner(vertex)) break;
      const next = incident.get(vertex)!.find((e) => !visited.has(e));
      if (next === undefined) break;
      edge = next;
    }
    const total = cumulative[cumulative.length - 1];
    let mid = 0;
    for (let i = 0; i < span.length; i++) {
      if (Math.abs(cumulative[i] - total / 2)
        < Math.abs(cumulative[mid] - total / 2)) mid = i;
    }
    const v = span[mid];
    if (!isCorner(v)) {
      targets.push({ vertex: v, kind: 'midpoint', pos: position(v) });
    }
    return vertex;
  };

  for (const v of incident.keys()) {
    if (isCorner(v)) {
      cornerSet.add(v);
      targets.push({ vertex: v, kind: 'corner', pos: position(v) });
    }
  }
  for (const corner of cornerSet) {
    for (const edge of incident.get(corner)!) {
      if (!visited.has(edge)) walkFrom(corner, edge);
    }
  }
  // cornerless loops (a full circle against a single neighbor): the walk
  // adds the loop's arclength-midpoint; keep the start vertex too so a
  // closed loop always offers two roughly opposite snap points
  for (const [key, [a]] of counts) {
    if (visited.has(key)) continue;
    walkFrom(a, key);
    targets.push({ vertex: a, kind: 'midpoint', pos: position(a) });
  }

  targetCache.set(cacheKey, targets);
  return targets;
}

export function bboxDiagonal(verts: Float32Array): number {
  let min = Infinity;
  let max = -Infinity;
  for (let i = 0; i < verts.length; i++) {
    if (verts[i] < min) min = verts[i];
    if (verts[i] > max) max = verts[i];
  }
  return (max - min) * Math.sqrt(3);
}

function nearestTarget(
  targets: SnapTarget[], point: [number, number, number], tolerance: number,
): SnapTarget | null {
  let best: SnapTarget | null = null;
  let bestDist = tolerance * tolerance;
  for (const t of targets) {
    const d = (t.pos[0] - point[0]) ** 2 + (t.pos[1] - point[1]) ** 2
      + (t.pos[2] - point[2]) ** 2;
    if (d < bestDist) {
      bestDist = d;
      best = t;
    }
  }
  return best;
}

// ------------------------------------------------------------ interaction

/**
 * Split-mode click handler, called from a plugin's `onPick` while the
 * host's assignment mode is active and `splitMode` is on. Two-click FSM:
 * select face -> arm start point -> commit cut. Always consumes the click.
 */
export function handleSplitPick(
  host: SplitHost, face: number, point: [number, number, number], ctx: ViewCtx,
): boolean {
  const { partId, viewerParams, setViewerParam } = useStore.getState();
  const desc = effectiveDescriptor(ctx.manifest);
  if (!partId || !desc) return false;

  void (async () => {
    try {
      const ids = await ctx.getField(desc) as Uint32Array;
      const params = viewerParams[host.processId] ?? {};
      const clicked = ids[face];

      if (params.splitFace == null || params.splitFace !== clicked) {
        setViewerParam(host.processId, 'splitFace', clicked);
        setViewerParam(host.processId, 'splitStart', null);
        return;
      }
      const targets = snapTargets(ctx, ids, clicked);
      const target = nearestTarget(
        targets, point, 0.03 * bboxDiagonal(ctx.verts));
      if (!target) return; // consumed, but no snap point near the click
      if (params.splitStart == null) {
        setViewerParam(host.processId, 'splitStart', target.vertex);
        return;
      }
      if (params.splitStart === target.vertex) {
        setViewerParam(host.processId, 'splitStart', null); // un-arm
        return;
      }
      await postSplit(partId, {
        face: clicked, start: params.splitStart, end: target.vertex,
      });
      clearSplitSelection(host.processId);
      await afterSplitsChanged(host, { rerun: true });
    } catch (err) {
      useStore.getState().set({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  })();
  return true;
}

/** Refresh manifest/fields after a splits mutation; optionally re-run the
 * host's currently selected assignment analysis against the new labeling. */
export async function afterSplitsChanged(
  host: SplitHost, { rerun }: { rerun: boolean },
): Promise<void> {
  splitsCache = null;
  targetCache.clear();
  await refreshManifest();
  if (rerun) await resubmitAssignment(host);
}

/**
 * Re-run the host's selected assignment result with its own parameters —
 * the recompute differs only in the splits state. On completion the
 * result selector snaps to the fresh result and the old result's
 * overrides carry forward (ids are stable under appended cuts; entries on
 * retired faces turn inert).
 */
export async function resubmitAssignment(host: SplitHost): Promise<void> {
  const { partId, manifest, catalog, viewerParams } = useStore.getState();
  if (!partId || !manifest) return;
  const result = host.currentResult(manifest, viewerParams[host.processId] ?? {});
  if (!result) return;
  const analysisId = host.analysisOf(result);
  const declared = catalog.find((p) => p.id === host.processId)
    ?.analyses.find((a) => a.id === analysisId)?.params.map((p) => p.name) ?? [];
  const params = Object.fromEntries(Object.entries(result.params)
    .filter(([name]) => declared.includes(name)));

  await runAnalysisJob(partId, host.processId, analysisId, params, async () => {
    const store = useStore.getState();
    const fresh = store.manifest?.results.filter(
      (r) => r.process === result.process && r.analysis === result.analysis
        && !!r.stats.verdict === !!result.stats.verdict).pop();
    if (fresh && fresh.hash !== result.hash) {
      const oldKey = `${result.process}.${result.analysis}.${result.hash}`;
      const newKey = `${fresh.process}.${fresh.analysis}.${fresh.hash}`;
      const carried = store.overrides[oldKey];
      if (carried && Object.keys(carried).length) {
        store.set({ overrides: { ...store.overrides, [newKey]: carried } });
        if (fresh.overrides_url) {
          await putOverrides(fresh.overrides_url, carried).catch(() => {});
        }
      }
    }
    store.setViewerParam(host.processId, host.resultParam, -1); // latest
  });
}

/** Undo the last cut / clear all cuts (SplitControls buttons). No re-run:
 * results computed for the reverted state become current again on their
 * own (the splits fingerprint replays byte-identically). */
export async function undoLastCut(host: SplitHost): Promise<void> {
  const { partId } = useStore.getState();
  if (!partId) return;
  await deleteLastSplit(partId);
  clearSplitSelection(host.processId);
  await afterSplitsChanged(host, { rerun: false });
}

export async function clearAllCuts(host: SplitHost): Promise<void> {
  const { partId } = useStore.getState();
  if (!partId) return;
  await clearSplits(partId);
  clearSplitSelection(host.processId);
  await afterSplitsChanged(host, { rerun: false });
}

// --------------------------------------------------------------- overlays

/**
 * Draw committed cut polylines and, while split mode is armed, the snap
 * markers of the selected face. Called from the host mode's paint; returns
 * extra stats lines.
 */
export async function drawSplitOverlays(
  ctx: ViewCtx, host: SplitHost, ids: Uint32Array,
): Promise<string[]> {
  const lines: string[] = [];
  const params = ctx.params;

  let state: SplitsState | null = null;
  try {
    state = await loadSplits(ctx);
  } catch {
    return lines; // no BREP data / request failed — nothing to draw
  }

  if (state.cuts.length) {
    const segments: number[] = [];
    for (const cut of state.cuts) {
      for (let i = 0; i + 1 < cut.polyline.length; i++) {
        segments.push(...cut.polyline[i], ...cut.polyline[i + 1]);
      }
    }
    ctx.setLines(new Float32Array(segments), CUT_COLOR);
    lines.push(`${state.cuts.length} cut(s)`
      + (state.stale ? ' — ⚠ splits reference an old mesh, clear all cuts' : ''));
  }

  if (params.splitMode && params.splitFace != null) {
    const face = params.splitFace as number;
    const targets = snapTargets(ctx, ids, face);
    const nodes = new Float32Array(targets.length * 3);
    targets.forEach((t, i) => nodes.set(t.pos, 3 * i));
    const radius = 0.008 * bboxDiagonal(ctx.verts);
    const { partId, manifestVersion } = useStore.getState();
    ctx.setGraph(
      `split:${partId}:${manifestVersion}:${face}:${params.splitStart ?? ''}`,
      nodes, new Uint32Array(0),
      new Float32Array(targets.length).fill(radius));
    ctx.paintGraph((n) => (targets[n].vertex === params.splitStart
      ? ARMED_COLOR
      : targets[n].kind === 'corner' ? CORNER_COLOR : MIDPOINT_COLOR));
    lines.push(params.splitStart == null
      ? 'split: click a marked boundary point (white = corner, gold = midpoint)'
      : 'split: click a second point to cut — same point cancels');
  } else if (params.splitMode) {
    lines.push('split: click a face to show its snap points');
  }
  return lines;
}
