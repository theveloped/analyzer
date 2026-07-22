// Pure voxel helpers for the Voxel render style — no three.js, unit-testable.
// prep/voxels stores the part's interior cells as linear C-order indices
// (lin = (ix*ny + iy)*nz + iz) plus grid meta; for display only the SURFACE
// shell matters (a solid part's deep interior is invisible anyway and would
// multiply the instance count), so cells with all six neighbours present
// are dropped.

export interface VoxelGridMeta {
  origin: [number, number, number];
  /** Cell pitch, mm. */
  voxel: number;
  dims: [number, number, number];
}

/** Centre positions (M*3) of the surface subset of the interior cells:
 * cells missing at least one of their six neighbours (grid-boundary cells
 * count as surface). */
export function surfaceVoxelCenters(
  index: Uint32Array, grid: VoxelGridMeta,
): Float32Array {
  const [, ny, nz] = grid.dims;
  const occupied = new Set<number>(index);
  const surface: number[] = [];
  for (let n = 0; n < index.length; n++) {
    const lin = index[n];
    const iz = lin % nz;
    const iy = ((lin - iz) / nz) % ny;
    // z/y neighbours need explicit bounds (±1/±nz would wrap into the next
    // column/row); x is the leading dimension, so ±ny*nz can't wrap — an
    // out-of-grid index is simply never occupied
    const solid = iz > 0 && iz < nz - 1 && occupied.has(lin - 1)
      && occupied.has(lin + 1)
      && iy > 0 && iy < ny - 1 && occupied.has(lin - nz)
      && occupied.has(lin + nz)
      && occupied.has(lin - ny * nz) && occupied.has(lin + ny * nz);
    if (!solid) surface.push(lin);
  }
  const [ox, oy, oz] = grid.origin;
  const h = grid.voxel;
  const positions = new Float32Array(surface.length * 3);
  for (let n = 0; n < surface.length; n++) {
    const lin = surface[n];
    const iz = lin % nz;
    const rest = (lin - iz) / nz;
    const iy = rest % ny;
    const ix = (rest - iy) / ny;
    positions[3 * n] = ox + (ix + 0.5) * h;
    positions[3 * n + 1] = oy + (iy + 0.5) * h;
    positions[3 * n + 2] = oz + (iz + 0.5) * h;
  }
  return positions;
}
