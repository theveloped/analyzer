// Sprue/gate proposal loading and marker-graph assembly.
//
// The sprue_proposals analysis stores ranked gate proposals (in stats, with
// per-metric subscores and human-readable reasons) plus per-candidate arrays
// over a "sprue" marker graph. Every result is bound to the exact
// wall_skeleton result it screened (stats.skeleton_hash), so the interactive
// fill solve reproduces the ranking's flow model node for node.

import type { ResultEntry } from '../../api/types';
import type { ViewCtx } from '../../registry/types';
import { percentile } from '../../colorizers/core';
import { loadSkeleton, skeletonResults, type Skeleton } from './skeleton';

export interface Proposal {
  rank: number;
  vertex: number;
  face: number;
  node: number;
  point: [number, number, number];
  score: number;
  subscores: Record<string, number>;
  raw: Record<string, number>;
  side: string;
  parting_distance: number | null;
  gate_style: 'edge' | 'hot_tip' | 'unknown';
  reasons: { pros: string[]; cons: string[] };
}

export interface SprueData {
  result: ResultEntry;
  skeleton: Skeleton;
  proposals: Proposal[];
  candidatePoints: Float32Array; // K x 3
  candidateScore: Float32Array; // K
  candidateNode: Uint32Array; // K -> skeleton node
}

export function sprueResults(ctx: ViewCtx): ResultEntry[] {
  return ctx.manifest.results.filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'sprue_proposals'
      && r.stats.schema === 1);
}

function fieldDesc(ctx: ViewCtx, result: ResultEntry, name: string) {
  const id = result.fields.find((f) => f.endsWith(`.${name}`));
  const desc = id && ctx.manifest.fields.find((f) => f.id === id);
  if (!desc) throw new Error(`sprue field ${name} missing from the manifest`);
  return desc;
}

export async function loadSprue(ctx: ViewCtx, result: ResultEntry): Promise<SprueData> {
  const skelResult = skeletonResults(ctx).find(
    (r) => r.hash === result.stats.skeleton_hash);
  if (!skelResult) {
    throw new Error('the wall_skeleton result this ranking used is gone — '
      + 're-run sprue proposals');
  }
  const [skeleton, candidatePoints, candidateScore, candidateNode] =
    await Promise.all([
      loadSkeleton(ctx, skelResult, 'cluster'),
      ctx.getField(fieldDesc(ctx, result, 'candidate_points')) as Promise<Float32Array>,
      ctx.getField(fieldDesc(ctx, result, 'candidate_score')) as Promise<Float32Array>,
      ctx.getField(fieldDesc(ctx, result, 'candidate_node')) as Promise<Uint32Array>,
    ]);
  return {
    result,
    skeleton,
    proposals: (result.stats.proposals ?? []) as Proposal[],
    candidatePoints,
    candidateScore,
    candidateNode,
  };
}

/**
 * Combined graph buffers: the skeleton plus marker points appended after the
 * skeleton nodes (edges only reference skeleton nodes, so markers render as
 * unconnected dots). Markers get a radius above the p98 node radius so the
 * point shader draws them as the biggest dots in view.
 */
export function markerGraph(sk: Skeleton, markers: Float32Array) {
  const nodeCount = sk.nodes.length / 3;
  const markerCount = markers.length / 3;
  const nodes = new Float32Array(sk.nodes.length + markers.length);
  nodes.set(sk.nodes);
  nodes.set(markers, sk.nodes.length);
  const radii = new Float32Array(nodeCount + markerCount);
  radii.set(sk.radii);
  radii.fill(3 * Math.max(percentile(sk.radii, 0.98), 1e-3), nodeCount);
  return { nodes, radii, markerBase: nodeCount };
}

/**
 * Client-side meeting-edge detection for the weld overlay: an edge where two
 * flow fronts arrive with less than the edge's own resistance between them
 * was crossed by neither front (matches gating.py's indicator modulo the
 * predecessor tie-break).
 */
export function weldSegments(sk: Skeleton, dist: Float32Array): Float32Array {
  const segments: number[] = [];
  for (let e = 0; e < sk.edges.length / 2; e++) {
    const a = sk.edges[2 * e];
    const b = sk.edges[2 * e + 1];
    const du = dist[a];
    const dv = dist[b];
    if (!isFinite(du) || !isFinite(dv)) continue;
    const dx = sk.nodes[3 * a] - sk.nodes[3 * b];
    const dy = sk.nodes[3 * a + 1] - sk.nodes[3 * b + 1];
    const dz = sk.nodes[3 * a + 2] - sk.nodes[3 * b + 2];
    const length = Math.sqrt(dx * dx + dy * dy + dz * dz);
    const radius = Math.max(0.5 * (sk.radii[a] + sk.radii[b]), 1e-3);
    const weight = length / radius ** 4;
    // margin: f32 arrivals make tree edges (|du-dv| == weight) fuzzy
    if (Math.abs(du - dv) >= weight * (1 - 1e-3)) continue;
    segments.push(
      sk.nodes[3 * a], sk.nodes[3 * a + 1], sk.nodes[3 * a + 2],
      sk.nodes[3 * b], sk.nodes[3 * b + 1], sk.nodes[3 * b + 2]);
  }
  return new Float32Array(segments);
}
