import { describe, expect, it } from 'vitest';
import { surfaceVoxelCenters, type VoxelGridMeta } from './voxels';

const lin = (ix: number, iy: number, iz: number, ny: number, nz: number) =>
  (ix * ny + iy) * nz + iz;

describe('surfaceVoxelCenters', () => {
  it('drops fully enclosed cells and keeps the shell', () => {
    // a solid 3x3x3 cube inside a 5x5x5 grid: 27 cells, 1 enclosed centre
    const grid: VoxelGridMeta = { origin: [0, 0, 0], voxel: 2, dims: [5, 5, 5] };
    const cells: number[] = [];
    for (let x = 1; x <= 3; x++) {
      for (let y = 1; y <= 3; y++) {
        for (let z = 1; z <= 3; z++) cells.push(lin(x, y, z, 5, 5));
      }
    }
    const positions = surfaceVoxelCenters(new Uint32Array(cells), grid);
    expect(positions.length / 3).toBe(26); // 27 minus the enclosed centre
    // no position may be the centre cell (2,2,2) → (5,5,5) mm
    for (let n = 0; n < positions.length; n += 3) {
      expect([positions[n], positions[n + 1], positions[n + 2]])
        .not.toEqual([5, 5, 5]);
    }
  });

  it('treats grid-boundary cells as surface', () => {
    // a single cell at the grid corner has missing neighbours on 3 sides
    const grid: VoxelGridMeta = { origin: [10, 0, -4], voxel: 1, dims: [2, 2, 2] };
    const positions = surfaceVoxelCenters(
      new Uint32Array([lin(0, 0, 0, 2, 2)]), grid);
    expect(Array.from(positions)).toEqual([10.5, 0.5, -3.5]);
  });
});
