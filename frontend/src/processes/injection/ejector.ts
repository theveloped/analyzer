// Ejector-pin data loading and the memoized interactive simulation call.
//
// The ejection_sticking analysis stores draft angles, the sticking-force
// heatmap and per-skeleton-node loads, bound to the exact wall_skeleton
// result it aggregated over (stats.skeleton_hash). Pin layouts are solved
// by POST /ejector/simulate on the backend (scipy over the same cached
// arrays); the response is memoized on the request body so unrelated
// viewer-param changes never re-POST.

import { postEjectorSimulate } from '../../api/client';
import type { ResultEntry } from '../../api/types';
import type { ViewCtx } from '../../registry/types';
import { loadSkeleton, skeletonResults, type Skeleton } from './skeleton';

export interface Pin {
  point: [number, number, number];
  diameter: number;
}

export interface EjectorPinResult {
  index: number;
  node: number;
  footprint: number;
  distance: number;
  force_n: number;
  pressure_mpa: number;
  utilization: number;
  over_limit: boolean;
}

export interface EjectorSimResponse {
  result_hash: string;
  skeleton_hash: string;
  nodes: number;
  deflection: (number | null)[];
  pins: EjectorPinResult[];
  stats: {
    total_sticking_n: number;
    supported_load_n: number;
    max_deflection_mm: number;
    p95_deflection_mm: number;
    unsupported: { nodes: number; load_n: number }[];
    E_mpa: number;
    allowable_pressure_mpa: number;
  };
}

export interface StickingData {
  result: ResultEntry;
  skeleton: Skeleton;
  vertForce: Float32Array;
  draftDeg: Float32Array;
  nodeLoad: Float32Array;
}

export function stickingResults(ctx: ViewCtx): ResultEntry[] {
  return ctx.manifest.results.filter(
    (r) => r.process === 'injection_molding'
      && r.analysis === 'ejection_sticking' && r.stats.schema === 2);
}

function fieldDesc(ctx: ViewCtx, result: ResultEntry, name: string) {
  const id = result.fields.find((f) => f.endsWith(`.${name}`));
  const desc = id && ctx.manifest.fields.find((f) => f.id === id);
  if (!desc) throw new Error(`sticking field ${name} missing from the manifest`);
  return desc;
}

export async function loadSticking(
  ctx: ViewCtx, result: ResultEntry,
): Promise<StickingData> {
  const skelResult = skeletonResults(ctx).find(
    (r) => r.hash === result.stats.skeleton_hash);
  if (!skelResult) {
    throw new Error('the wall_skeleton result this analysis used is gone — '
      + 're-run ejection sticking');
  }
  const [skeleton, vertForce, draftDeg, nodeLoad] = await Promise.all([
    loadSkeleton(ctx, skelResult, 'cluster'),
    ctx.getField(fieldDesc(ctx, result, 'vert_force')) as Promise<Float32Array>,
    ctx.getField(fieldDesc(ctx, result, 'draft_deg')) as Promise<Float32Array>,
    ctx.getField(fieldDesc(ctx, result, 'node_load')) as Promise<Float32Array>,
  ]);
  return { result, skeleton, vertForce, draftDeg, nodeLoad };
}

let memo: { key: string; promise: Promise<EjectorSimResponse> } | null = null;

/** POST the pin layout, memoized on the exact request body: the controller
 * repaints on every viewer-param change, and only a changed layout should
 * hit the backend again. */
export function simulateCached(
  partId: string, body: Record<string, any>,
): Promise<EjectorSimResponse> {
  const key = `${partId}:${JSON.stringify(body)}`;
  if (memo?.key !== key) {
    memo = { key, promise: postEjectorSimulate<EjectorSimResponse>(partId, body) };
    // a failed solve must not stick in the memo
    memo.promise.catch(() => {
      if (memo?.key === key) memo = null;
    });
  }
  return memo.promise;
}
