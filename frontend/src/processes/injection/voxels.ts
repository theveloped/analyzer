// Voxel grid loading for the flow_voxels / flow_fill results.
//
// flow_voxels stores the interior voxel set of a signed-distance grid as
// linear C-order cell indices plus the per-voxel wall distance; positions
// are reconstructed client-side from the grid meta (origin + (i + 0.5) * h)
// so the wire payload stays two compact arrays. flow_fill adds per-voxel
// arrival/frozen fields on the identical grid (bound by stats.voxels_hash)
// and their surface projections through vert_voxel.

import type { Manifest, ResultEntry } from '../../api/types';
import type { ViewCtx } from '../../registry/types';

/** keep in sync with FLOW_SCHEMA in processes/injection_molding.py */
export const FLOW_SCHEMA = 1;

/** vert_voxel entry for a vertex with no interior voxel */
export const VOXEL_SENTINEL = 0xffffffff;

/** vert_frozen code: filled fine */
export const FROZEN_OK = 255;
/** vert_frozen code: channel below grid resolution — not judgeable */
export const FROZEN_UNJUDGED = 254;

export interface VoxelGrid {
  key: string;
  origin: [number, number, number];
  h: number; // voxel size, mm
  dims: [number, number, number];
  index: Uint32Array; // linear C-order cell index per interior voxel
  dist: Float32Array; // distance to the mold wall per voxel, mm
}

export interface FlowFill {
  key: string;
  arrival: Float32Array; // seconds per voxel, Infinity = unreached
  frozen: Uint8Array; // 255 ok, 0 never reached, k = lost at pass k
  vertArrival: Float32Array; // seconds per vertex, NaN = unreached/unmapped
  vertFrozen: Uint8Array; // frozen code through the ridge voxel (254 = unjudged)
}

export function flowVoxelResults(manifest: Manifest): ResultEntry[] {
  return manifest.results.filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'flow_voxels'
      && r.params.schema === FLOW_SCHEMA);
}

export function flowFillResults(manifest: Manifest): ResultEntry[] {
  return manifest.results.filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'flow_fill'
      && r.params.schema === FLOW_SCHEMA);
}

function fieldDesc(ctx: ViewCtx, result: ResultEntry, name: string) {
  const id = result.fields.find((f) => f.endsWith(`.${name}`));
  const desc = id && ctx.manifest.fields.find((f) => f.id === id);
  if (!desc) throw new Error(`flow field ${name} missing from the manifest`);
  return desc;
}

export async function loadVoxelGrid(
  ctx: ViewCtx, result: ResultEntry,
): Promise<VoxelGrid> {
  const indexDesc = fieldDesc(ctx, result, 'voxel_index');
  const grid = indexDesc.params.grid;
  const [index, dist] = await Promise.all([
    ctx.getField(indexDesc) as Promise<Uint32Array>,
    ctx.getField(fieldDesc(ctx, result, 'voxel_dist')) as Promise<Float32Array>,
  ]);
  return {
    key: result.hash,
    origin: grid.origin,
    h: grid.voxel,
    dims: grid.dims,
    index,
    dist,
  };
}

export async function loadVertVoxel(
  ctx: ViewCtx, result: ResultEntry,
): Promise<{ vertVoxel: Uint32Array; vertHalf: Float32Array }> {
  const [vertVoxel, vertHalf] = await Promise.all([
    ctx.getField(fieldDesc(ctx, result, 'vert_voxel')) as Promise<Uint32Array>,
    ctx.getField(
      fieldDesc(ctx, result, 'vert_half_thickness')) as Promise<Float32Array>,
  ]);
  return { vertVoxel, vertHalf };
}

export async function loadFill(
  ctx: ViewCtx, result: ResultEntry,
): Promise<FlowFill> {
  const [arrival, frozen, vertArrival, vertFrozen] = await Promise.all([
    ctx.getField(fieldDesc(ctx, result, 'arrival')) as Promise<Float32Array>,
    ctx.getField(fieldDesc(ctx, result, 'frozen')) as Promise<Uint8Array>,
    ctx.getField(fieldDesc(ctx, result, 'vert_arrival')) as Promise<Float32Array>,
    ctx.getField(fieldDesc(ctx, result, 'vert_frozen')) as Promise<Uint8Array>,
  ]);
  return { key: result.hash, arrival, frozen, vertArrival, vertFrozen };
}

// positions/sizes are pure functions of the grid; cache a few grids like
// the skeleton adjacency cache so repaints never rebuild them
const positionCache = new Map<string, Float32Array>();

/** Voxel center positions (N x 3) decoded from the linear cell indices. */
export function voxelPositions(grid: VoxelGrid): Float32Array {
  const cached = positionCache.get(grid.key);
  if (cached) return cached;

  const [, ny, nz] = grid.dims;
  const [ox, oy, oz] = grid.origin;
  const { h, index } = grid;
  const positions = new Float32Array(index.length * 3);
  for (let n = 0; n < index.length; n++) {
    const lin = index[n];
    const iz = lin % nz;
    const rest = (lin - iz) / nz;
    const iy = rest % ny;
    const ix = (rest - iy) / ny;
    positions[3 * n] = ox + (ix + 0.5) * h;
    positions[3 * n + 1] = oy + (iy + 0.5) * h;
    positions[3 * n + 2] = oz + (iz + 0.5) * h;
  }

  positionCache.set(grid.key, positions);
  if (positionCache.size > 4) {
    positionCache.delete(positionCache.keys().next().value!);
  }
  return positions;
}
