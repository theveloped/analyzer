import { describe, expect, it } from 'vitest';
import { nearestCorner, snapSection } from './sectionSnap';

const dot = (a: number[], b: number[]) =>
  a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

describe('snapSection', () => {
  it('aligns to a planar face through the picked point', () => {
    const r = snapSection(
      { type: 'plane', normal: [0, 0, 2] }, // non-unit on purpose
      [3, 4, 5], [1, 0, 0], [1, 0, 0]);
    expect(r.normal).toEqual([0, 0, 1]);
    expect(r.offset).toBeCloseTo(5, 12);
  });

  it('puts the plane through a cylinder axis, perpendicular to it', () => {
    const r = snapSection(
      { type: 'cylinder', point: [10, 0, 3], axis: [0, 0, 1], radius: 4 } as never,
      [14, 0, 5], [0.6, 0.8, 0], [1, 0, 0]);
    // normal ⟂ axis and the plane contains the anchor
    expect(dot(r.normal, [0, 0, 1])).toBeCloseTo(0, 12);
    expect(dot(r.normal, [10, 0, 3])).toBeCloseTo(r.offset, 12);
    // faces the camera: the view direction's perpendicular component
    expect(r.normal[0]).toBeCloseTo(0.6, 9);
    expect(r.normal[1]).toBeCloseTo(0.8, 9);
  });

  it('handles a view direction parallel to the axis (any perpendicular)', () => {
    const r = snapSection(
      { type: 'cylinder', point: [0, 0, 0], axis: [0, 0, 1] },
      [1, 0, 0], [0, 0, -1], [1, 0, 0]);
    expect(dot(r.normal, [0, 0, 1])).toBeCloseTo(0, 12);
    expect(Math.hypot(...r.normal)).toBeCloseTo(1, 12);
  });

  it('translates the current plane through a sphere center', () => {
    const r = snapSection(
      { type: 'sphere', center: [2, 4, 6] }, [9, 9, 9], [1, 0, 0], [0, 1, 0]);
    expect(r.normal).toEqual([0, 1, 0]);
    expect(r.offset).toBeCloseTo(4, 12);
  });

  it('falls back to a point snap for freeform faces', () => {
    const r = snapSection(null, [1, 2, 3], [1, 0, 0], [0, 0, 1]);
    expect(r.normal).toEqual([0, 0, 1]);
    expect(r.offset).toBeCloseTo(3, 12);
  });
});

describe('nearestCorner', () => {
  it('returns the triangle corner closest to the hit', () => {
    const verts = new Float32Array([0, 0, 0, 10, 0, 0, 0, 10, 0]);
    const faces = new Uint32Array([0, 1, 2]);
    expect(nearestCorner(verts, faces, 0, [8, 1, 0])).toEqual([10, 0, 0]);
    expect(nearestCorner(verts, faces, 0, [1, 1, 0])).toEqual([0, 0, 0]);
  });
});
