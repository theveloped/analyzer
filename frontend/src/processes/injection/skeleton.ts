// Skeleton graph loading and the client-side fill-flow solve.
//
// The wall_skeleton analysis stores two graphs (raw = one node per mesh
// vertex's inscribed-sphere center, clustered = merged medial nodes) plus a
// vertex -> node map for painting graph results back onto the mesh. Fill
// flow is a Dijkstra over Hagen-Poiseuille-style edge resistances
// (length / r^4), cheap enough to rerun on every gate click.

import type { FieldDescriptor, ResultEntry } from '../../api/types';
import type { ViewCtx } from '../../registry/types';

/** raw_vert_node / cluster_vert_node entry for a vertex with no node */
export const SENTINEL = 0xffffffff;

export interface Skeleton {
  key: string;
  nodes: Float32Array; // N x 3 centers
  radii: Float32Array; // N inscribed-sphere radii
  edges: Uint32Array; // M x 2 node index pairs
  vertNode: Uint32Array; // mesh vertex -> node (SENTINEL = none)
}

export function skeletonResults(ctx: ViewCtx): ResultEntry[] {
  return ctx.manifest.results.filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'wall_skeleton');
}

function fieldDesc(ctx: ViewCtx, result: ResultEntry, name: string): FieldDescriptor {
  const id = result.fields.find((f) => f.endsWith(`.${name}`));
  const desc = id && ctx.manifest.fields.find((f) => f.id === id);
  if (!desc) throw new Error(`skeleton field ${name} missing from the manifest`);
  return desc;
}

export async function loadSkeleton(
  ctx: ViewCtx, result: ResultEntry, which: 'raw' | 'cluster',
): Promise<Skeleton> {
  const [nodes, radii, edges, vertNode] = await Promise.all([
    ctx.getField(fieldDesc(ctx, result, `${which}_nodes`)) as Promise<Float32Array>,
    ctx.getField(fieldDesc(ctx, result, `${which}_radii`)) as Promise<Float32Array>,
    ctx.getField(fieldDesc(ctx, result, `${which}_edges`)) as Promise<Uint32Array>,
    ctx.getField(fieldDesc(ctx, result, `${which}_vert_node`)) as Promise<Uint32Array>,
  ]);
  return { key: `${result.hash}:${which}`, nodes, radii, edges, vertNode };
}

export function nearestNode(nodes: Float32Array, p: [number, number, number]): number {
  let best = 0;
  let bestDist = Infinity;
  for (let n = 0; n < nodes.length / 3; n++) {
    const dx = nodes[3 * n] - p[0];
    const dy = nodes[3 * n + 1] - p[1];
    const dz = nodes[3 * n + 2] - p[2];
    const d = dx * dx + dy * dy + dz * dz;
    if (d < bestDist) {
      bestDist = d;
      best = n;
    }
  }
  return best;
}

export interface Adjacency {
  offsets: Uint32Array; // CSR row offsets, length N+1
  neighbors: Uint32Array;
  weights: Float32Array;
}

const adjacencyCache = new Map<string, Adjacency>();

/**
 * CSR adjacency with Hagen-Poiseuille-ish resistances: pushing melt along
 * an edge costs its length over the local channel radius^4 (the mean of
 * the endpoint sphere radii). Units are relative, which is all a fill
 * *ordering* needs.
 */
export function buildAdjacency(key: string, sk: Skeleton): Adjacency {
  const cached = adjacencyCache.get(key);
  if (cached) return cached;

  const nodeCount = sk.nodes.length / 3;
  const edgeCount = sk.edges.length / 2;
  const degree = new Uint32Array(nodeCount + 1);
  for (let e = 0; e < edgeCount; e++) {
    degree[sk.edges[2 * e] + 1]++;
    degree[sk.edges[2 * e + 1] + 1]++;
  }
  const offsets = new Uint32Array(nodeCount + 1);
  for (let n = 0; n < nodeCount; n++) offsets[n + 1] = offsets[n] + degree[n + 1];

  const neighbors = new Uint32Array(offsets[nodeCount]);
  const weights = new Float32Array(offsets[nodeCount]);
  const cursor = offsets.slice(0, nodeCount);
  for (let e = 0; e < edgeCount; e++) {
    const a = sk.edges[2 * e];
    const b = sk.edges[2 * e + 1];
    const dx = sk.nodes[3 * a] - sk.nodes[3 * b];
    const dy = sk.nodes[3 * a + 1] - sk.nodes[3 * b + 1];
    const dz = sk.nodes[3 * a + 2] - sk.nodes[3 * b + 2];
    const length = Math.sqrt(dx * dx + dy * dy + dz * dz);
    const radius = Math.max(0.5 * (sk.radii[a] + sk.radii[b]), 1e-3);
    const weight = length / radius ** 4;
    neighbors[cursor[a]] = b;
    weights[cursor[a]++] = weight;
    neighbors[cursor[b]] = a;
    weights[cursor[b]++] = weight;
  }

  const adjacency = { offsets, neighbors, weights };
  adjacencyCache.set(key, adjacency);
  if (adjacencyCache.size > 8) {
    adjacencyCache.delete(adjacencyCache.keys().next().value!);
  }
  return adjacency;
}

/** Typed-array binary-heap Dijkstra; unreached nodes stay Infinity. */
export function dijkstra(adjacency: Adjacency, source: number): Float32Array {
  const nodeCount = adjacency.offsets.length - 1;
  const dist = new Float32Array(nodeCount).fill(Infinity);
  const heap = new Uint32Array(nodeCount); // decrease-key: each node once
  const position = new Int32Array(nodeCount).fill(-1);
  let heapSize = 0;

  const swap = (i: number, j: number) => {
    const a = heap[i];
    const b = heap[j];
    heap[i] = b;
    heap[j] = a;
    position[a] = j;
    position[b] = i;
  };
  const up = (i: number) => {
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (dist[heap[parent]] <= dist[heap[i]]) break;
      swap(i, parent);
      i = parent;
    }
  };
  const down = (i: number) => {
    for (;;) {
      let smallest = i;
      const left = 2 * i + 1;
      const right = 2 * i + 2;
      if (left < heapSize && dist[heap[left]] < dist[heap[smallest]]) smallest = left;
      if (right < heapSize && dist[heap[right]] < dist[heap[smallest]]) smallest = right;
      if (smallest === i) break;
      swap(i, smallest);
      i = smallest;
    }
  };

  dist[source] = 0;
  heap[heapSize] = source;
  position[source] = heapSize++;

  while (heapSize > 0) {
    const node = heap[0];
    swap(0, --heapSize);
    position[node] = -2; // settled
    down(0);
    for (let i = adjacency.offsets[node]; i < adjacency.offsets[node + 1]; i++) {
      const neighbor = adjacency.neighbors[i];
      if (position[neighbor] === -2) continue;
      const candidate = dist[node] + adjacency.weights[i];
      if (candidate < dist[neighbor]) {
        dist[neighbor] = candidate;
        if (position[neighbor] === -1) {
          heap[heapSize] = neighbor;
          position[neighbor] = heapSize++;
          up(position[neighbor]);
        } else {
          up(position[neighbor]);
        }
      }
    }
  }
  return dist;
}
