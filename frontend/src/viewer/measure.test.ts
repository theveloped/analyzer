import { describe, expect, it } from 'vitest';
import { computeMeasurement, type MeasurePick } from './measure';

const pick = (
  point: [number, number, number],
  normal: [number, number, number],
  faceIndex = 0,
): MeasurePick => ({ point, faceIndex, brepFace: null, normal });

describe('computeMeasurement', () => {
  it('reports straight-line distance and signed component deltas', () => {
    const r = computeMeasurement(
      pick([1, 2, 3], [0, 0, 1]), pick([4, -2, 3], [0, 0, 1]));
    expect(r.distance).toBeCloseTo(5, 12);
    expect(r.delta).toEqual([3, -4, 0]);
  });

  it('splits the delta into signed normal separation and in-plane offset', () => {
    // A's normal +Z; B sits 2 below and 3+4 sideways
    const r = computeMeasurement(
      pick([0, 0, 0], [0, 0, 1]), pick([3, 4, -2], [0, 0, 1]));
    expect(r.alongNormalA).toBeCloseTo(-2, 12); // signed: B is BELOW A's plane
    expect(r.inPlane).toBeCloseTo(5, 12);
  });

  it('reports pure normal separation with zero in-plane rest', () => {
    const r = computeMeasurement(
      pick([0, 0, 0], [0, 0, 1]), pick([0, 0, 7], [0, 0, -1]));
    expect(r.alongNormalA).toBeCloseTo(7, 12);
    expect(r.inPlane).toBeCloseTo(0, 12);
  });

  it('distinguishes parallel, opposed and orthogonal normals', () => {
    const parallel = computeMeasurement(
      pick([0, 0, 0], [0, 0, 1]), pick([1, 0, 0], [0, 0, 1]));
    expect(parallel.normalAngleDeg).toBeCloseTo(0, 9);
    expect(parallel.planeAngleDeg).toBeCloseTo(0, 9);

    // opposed normals (facing walls): directed angle 180°, but the PLANES
    // are parallel → orientation-independent plane angle 0°
    const opposed = computeMeasurement(
      pick([0, 0, 0], [0, 0, 1]), pick([0, 0, 5], [0, 0, -1]));
    expect(opposed.normalAngleDeg).toBeCloseTo(180, 9);
    expect(opposed.planeAngleDeg).toBeCloseTo(0, 9);

    const orthogonal = computeMeasurement(
      pick([0, 0, 0], [0, 0, 1]), pick([1, 1, 0], [1, 0, 0]));
    expect(orthogonal.normalAngleDeg).toBeCloseTo(90, 9);
    expect(orthogonal.planeAngleDeg).toBeCloseTo(90, 9);
  });

  it('survives zero-length picks (A === B)', () => {
    const r = computeMeasurement(
      pick([2, 2, 2], [0, 0, 1]), pick([2, 2, 2], [0, 1, 0]));
    expect(r.distance).toBe(0);
    expect(r.delta).toEqual([0, 0, 0]);
    expect(r.alongNormalA).toBe(0);
    expect(r.inPlane).toBe(0);
    expect(r.normalAngleDeg).toBeCloseTo(90, 9); // angles remain defined
  });

  it('normalizes non-unit normals before use', () => {
    const r = computeMeasurement(
      pick([0, 0, 0], [0, 0, 10]), pick([0, 0, 3], [0, 0, 0.5]));
    expect(r.alongNormalA).toBeCloseTo(3, 12);
    expect(r.normalAngleDeg).toBeCloseTo(0, 9);
  });

  it('never lets rounding push inPlane below zero', () => {
    // delta exactly along the normal: distance² − along² can round negative
    const r = computeMeasurement(
      pick([0.1, 0.2, 0.3], [0.6, 0.48, 0.64]),
      pick([0.7, 0.68, 0.94], [0.6, 0.48, 0.64]));
    expect(r.inPlane).toBeGreaterThanOrEqual(0);
    expect(r.inPlane).toBeCloseTo(0, 6);
  });
});
