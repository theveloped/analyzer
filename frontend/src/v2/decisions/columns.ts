// The directions overview: one row per candidate direction, one column per
// thing that can be known about it.
//
// Two rules shape this file:
//
//  * CANDIDATES ARE FREE. They come from the live client-side set the
//    directions lens paints — no job, no artifact, no index. The table is a
//    VIEW OVER WHATEVER THE CACHE HAPPENS TO HOLD for those directions, so it
//    is useful the moment you open it and fills in as you ask for things.
//  * A cell is computed on request, never in a sweep. Each cell knows the one
//    job that would fill it; nothing runs on its own.
//
// Cached results are MERGED rather than matched: every non-stale result of an
// analysis contributes whatever rows it has, newest winning. That is what lets
// "compute this one cell" accumulate into a full table instead of forcing one
// all-or-nothing run.

import type { Manifest, ResultEntry } from '../../api/types';
import type { GeneratedDir } from '../../processes/directions/build';
import { cncSources } from '../../processes/cnc/sources';
import { useStore } from '../../state/store';
import { runAnalysisJob } from '../../viewer/jobs';
import { DEFAULT_TOOLS } from '../checks/catalog';

/** Mirror of processes/cnc.py TURNING_SCAN_SCHEMA. */
export const TURNING_SCAN_SCHEMA = 1;

/** Two directions are the same candidate within the backend's own dedup
 * tolerance (analysis._dedup_seen uses 1 degree). */
const SAME_DIR = Math.cos((1.0 * Math.PI) / 180);

export interface ToolSpec {
  diameter: number;
  corner_radius: number;
  stickout: number | null;
  holder_radius: number | null;
}

export type CellState = 'value' | 'missing' | 'blocked';

/** How to show one cell in the viewer: the mode plus the params that pin it to
 * exactly this direction and this stored result. */
export interface CellLens {
  processId: string;
  modeId: string;
  params: Record<string, unknown>;
}

export interface Cell {
  state: CellState;
  value: number | null;
  /** Why it cannot be asked for yet (blocked only). */
  note?: string;
  /** Present once the value exists — clicking paints it. */
  lens?: CellLens;
}

export interface DirectionRow {
  key: string;
  vector: number[];
  label: string;
  source: string;
  /** Row in directions.npy, once an accessibility run has covered it. */
  index: number | null;
  selected: boolean;
  accessible: Cell;
  compatible: Cell;
  swept: Cell;
  qualified: boolean | null;
  reach: Cell[];
}

const dot = (a: number[], b: number[]) =>
  (a[0] ?? 0) * (b[0] ?? 0) + (a[1] ?? 0) * (b[1] ?? 0) + (a[2] ?? 0) * (b[2] ?? 0);

/** Row of directions.npy holding this direction, or null if none does. */
export function indexOf(manifest: Manifest | null, vector: number[]): number | null {
  const dirs = manifest?.directions ?? [];
  for (let i = 0; i < dirs.length; i++) {
    if (dot(dirs[i], vector) >= SAME_DIR) return i;
  }
  return null;
}

function freshResults(
  manifest: Manifest | null, process: string, analysis: string,
): ResultEntry[] {
  return (manifest?.results ?? []).filter(
    (r) => r.process === process && r.analysis === analysis && !r.stale);
}

export function toolKey(tool: ToolSpec): string {
  return [tool.diameter, tool.corner_radius, tool.stickout ?? '-',
    tool.holder_radius ?? '-'].join(':');
}

export function toolLabel(tool: ToolSpec): string {
  return `⌀${tool.diameter} ${tool.corner_radius ? `r${tool.corner_radius}` : 'flat'}`;
}

export function toolTitle(tool: ToolSpec): string {
  const parts = [`⌀${tool.diameter} mm`,
    tool.corner_radius ? `corner r${tool.corner_radius}` : 'flat end'];
  if (tool.stickout != null) parts.push(`stickout ${tool.stickout}`);
  if (tool.holder_radius != null) parts.push(`holder r${tool.holder_radius}`);
  return parts.join(' · ');
}

/** Tool columns: whatever the cache already knows about, plus the library, so
 * there is always something to ask for. */
export function toolColumns(manifest: Manifest | null): ToolSpec[] {
  const seen = new Map<string, ToolSpec>();
  for (const tool of DEFAULT_TOOLS as ToolSpec[]) seen.set(toolKey(tool), tool);
  for (const result of freshResults(manifest, 'cnc', 'reach_study')) {
    for (const tool of ((result.stats as any)?.tools ?? []) as ToolSpec[]) {
      seen.set(toolKey(tool), tool);
    }
  }
  return [...seen.values()];
}

export interface ReachHit {
  share: number;
  /** The result that actually holds this mask, and the tool's index INSIDE
   * it — `findStudy` takes the newest result when no hash is pinned, and
   * per-cell runs mean there are many. */
  hash: string;
  toolIndex: number;
}

/** (direction row, tool key) -> reach hit, merged over every non-stale reach
 * result; later results win. */
function reachIndex(manifest: Manifest | null): Map<string, ReachHit> {
  const out = new Map<string, ReachHit>();
  for (const result of freshResults(manifest, 'cnc', 'reach_study')) {
    const stats = result.stats as any;
    const total = Number(stats?.total_area) || 0;
    const tools: ToolSpec[] = stats?.tools ?? [];
    if (!total) continue;
    for (const pair of stats?.pairs ?? []) {
      const tool = tools[pair.tool];
      if (!tool) continue;
      out.set(`${pair.direction}:${toolKey(tool)}`, {
        share: pair.reachable_area / total,
        hash: result.hash,
        toolIndex: pair.tool,
      });
    }
  }
  return out;
}

export interface ScanHit {
  row: any;
  hash: string;
  /** Index of this axis within its result's `stats.axes`. */
  axis: number;
}

/** Scored axes merged over every non-stale scan; matched by direction because
 * the scan is keyed by vectors, not by any index space. An axis is a LINE, so
 * a direction and its opposite score the same. */
function scanRows(manifest: Manifest | null): { vector: number[]; hit: ScanHit }[] {
  const rows: { vector: number[]; hit: ScanHit }[] = [];
  for (const result of freshResults(manifest, 'cnc', 'turning_scan')) {
    ((result.stats as any)?.axes ?? []).forEach((axis: any, i: number) => {
      rows.push({
        vector: axis.vector ?? [],
        hit: { row: axis, hash: result.hash, axis: i },
      });
    });
  }
  return rows;
}

function findScan(
  rows: { vector: number[]; hit: ScanHit }[], vector: number[],
): ScanHit | null {
  for (let i = rows.length - 1; i >= 0; i--) {
    if (Math.abs(dot(rows[i].vector, vector)) >= SAME_DIR) return rows[i].hit;
  }
  return null;
}

/** `viewerParams.cnc.source` for a global direction row. It is an index into
 * `cncSources`, which re-sorts so directions with cached tool fields come
 * first — so it is NOT the direction index and must be looked up. */
export function sourceIndexOf(
  manifest: Manifest | null, direction: number,
): number {
  if (!manifest) return -1;
  return cncSources(manifest).findIndex((s) => s.direction === direction);
}

const value = (v: number, lens?: CellLens): Cell =>
  ({ state: 'value', value: v, lens });
const missing: Cell = { state: 'missing', value: null };

export function buildRows(
  manifest: Manifest | null,
  candidates: GeneratedDir[],
  selected: string[],
  tools: ToolSpec[],
): DirectionRow[] {
  const sources = manifest?.direction_sources ?? [];
  const scans = scanRows(manifest);
  const reach = reachIndex(manifest);
  const chosen = new Set(selected);
  const meshed = !!manifest?.mesh;

  return candidates.map((candidate) => {
    const index = indexOf(manifest, candidate.vector);
    const provenance = candidate.provenances[0];
    const axis = findScan(scans, candidate.vector);
    const share = index == null
      ? null : sources[index]?.accessible_fraction ?? null;

    const needsMesh = (cell: Cell): Cell => (meshed ? cell : {
      state: 'blocked', value: null,
      note: 'needs the fine mesh — open a lens that builds it first',
    });

    // one lens per turnability result, shared by both of its columns
    const axisLens: CellLens | undefined = axis ? {
      processId: 'cnc',
      modeId: 'axis_role',
      params: { scanHash: axis.hash, scanAxis: axis.axis },
    } : undefined;
    const sourceIndex = index == null ? -1 : sourceIndexOf(manifest, index);
    const accessLens: CellLens | undefined = sourceIndex >= 0 ? {
      processId: 'cnc',
      modeId: 'access',
      // tip resets with the source, as the v1 controls do
      params: { source: sourceIndex, tip: 0 },
    } : undefined;

    return {
      key: candidate.key,
      vector: candidate.vector,
      label: provenance?.label ?? 'direction',
      source: provenance?.source ?? 'uniform',
      index,
      selected: chosen.has(candidate.key),
      accessible: needsMesh(
        share == null ? missing : value(share, accessLens)),
      compatible: needsMesh(
        axis ? value(axis.row.inlier_fraction, axisLens) : missing),
      swept: needsMesh(
        axis ? value(axis.row.radial_fraction, axisLens) : missing),
      qualified: axis ? !!axis.row.qualified : null,
      reach: tools.map((tool) => {
        if (index == null) return needsMesh(missing);
        const hit = reach.get(`${index}:${toolKey(tool)}`);
        if (hit == null) return needsMesh(missing);
        return needsMesh(value(hit.share, {
          processId: 'cnc',
          modeId: 'reach_study',
          params: {
            reachHash: hit.hash,
            reachDirection: index,
            reachTool: hit.toolIndex,
          },
        }));
      }),
    };
  });
}

// --- filling one cell ------------------------------------------------------

/** prep/directions params that assemble exactly the candidate set: every
 * candidate as a manual vector, no generators. The backend antipodal-pairs
 * and dedups them, which is why the table joins by vector rather than
 * assuming a position. */
function directionSetParams(candidates: GeneratedDir[]) {
  return {
    count: 0, axes: false, bbox_axes: false, hole_axes: false,
    face_groups: [], manual: candidates.map((c) => c.vector),
  };
}

/** Compute the turnability of ONE candidate — vectors in, nothing else
 * needed, so this never waits on an accessibility run. */
export async function runTurnability(
  row: DirectionRow, onDone?: () => void | Promise<void>,
): Promise<void> {
  const partId = useStore.getState().partId;
  if (!partId) return;
  await runAnalysisJob(partId, 'cnc', 'turning_scan',
    { axis_vectors: [row.vector] }, onDone);
}

/** Accessibility is a property of the SET: the visibility raster is computed
 * per direction, but the artifact it lands in is one array over all of them.
 * So this is the one action that necessarily covers every candidate. */
export async function runAccessibility(
  candidates: GeneratedDir[], onDone?: () => void | Promise<void>,
): Promise<void> {
  const partId = useStore.getState().partId;
  if (!partId || !candidates.length) return;
  await runAnalysisJob(partId, 'prep', 'directions',
    directionSetParams(candidates), onDone);
}

/** Reach for one (direction, tool). Chains the direction set first when the
 * candidate has no row in directions.npy yet — reach masks are indexed by it. */
export async function runReach(
  row: DirectionRow, tool: ToolSpec, candidates: GeneratedDir[],
  onDone?: () => void | Promise<void>,
): Promise<void> {
  const partId = useStore.getState().partId;
  if (!partId) return;
  const submit = async () => {
    const index = indexOf(useStore.getState().manifest, row.vector);
    if (index == null) return;
    await runAnalysisJob(partId, 'cnc', 'reach_study',
      { direction_indices: [index], tools: [tool] }, onDone);
  };
  if (row.index != null) {
    await submit();
    return;
  }
  await runAnalysisJob(partId, 'prep', 'directions',
    directionSetParams(candidates), submit);
}

// --- showing a cell --------------------------------------------------------

export function cellOf(row: DirectionRow, column: string): Cell {
  if (column === 'accessible') return row.accessible;
  if (column === 'compatible') return row.compatible;
  if (column === 'swept') return row.swept;
  if (column.startsWith('tool')) {
    return row.reach[Number(column.slice(4))] ?? { state: 'blocked', value: null };
  }
  return { state: 'blocked', value: null };
}

function activate(lens: CellLens): void {
  const store = useStore.getState();
  for (const [name, v] of Object.entries(lens.params)) {
    store.setViewerParam(lens.processId, name, v);
  }
  store.set({ processId: lens.processId, modeId: lens.modeId });
}

/** Show a cell in the viewer — computing it first when it is missing, then
 * painting the result that just landed. The table's number fills in from the
 * same manifest refresh, so the click both explains and answers. */
export function openCell(
  row: DirectionRow, column: string, candidates: GeneratedDir[],
  tools: ToolSpec[],
): void {
  const cell = cellOf(row, column);
  if (cell.state === 'blocked') return;
  if (cell.lens) {
    activate(cell.lens);
    return;
  }
  // re-derive the row from the refreshed manifest: the lens can only be built
  // once the result exists, and the run is what makes it exist
  const paintWhenReady = () => {
    const manifest = useStore.getState().manifest;
    const fresh = buildRows(manifest, candidates, [], toolColumns(manifest))
      .find((r) => r.key === row.key);
    const lens = fresh && cellOf(fresh, column).lens;
    if (lens) activate(lens);
  };
  if (column === 'accessible') {
    void runAccessibility(candidates, paintWhenReady);
  } else if (column === 'compatible' || column === 'swept') {
    void runTurnability(row, paintWhenReady);
  } else if (column.startsWith('tool')) {
    const tool = tools[Number(column.slice(4))];
    if (tool) void runReach(row, tool, candidates, paintWhenReady);
  }
}
