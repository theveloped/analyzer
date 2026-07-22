// Pure measurement math — no three.js, no DOM, fully unit-testable.
// Two picks A/B captured from the rendered (posed) geometry produce the
// readouts the measure rail reports. A face normal does not define unique
// tangent axes, so no local frame is invented; and two mesh picks do NOT
// establish the minimum distance between the complete BREP faces — callers
// must label the distance "picked-point distance".

export interface MeasurePick {
  /** Posed world position of the pick. */
  point: [number, number, number];
  /** Fine-mesh triangle index. */
  faceIndex: number;
  /** Effective BREP/subface id when cached, null otherwise (e.g. STL). */
  brepFace: number | null;
  /** Surface normal at pick time (unit; normalized defensively anyway). */
  normal: [number, number, number];
}

/** How the A→B delta is decomposed into component legs. */
export type MeasureFrame = 'xyz' | 'normalA' | 'normalB';

export interface MeasureReadout {
  /** Straight-line picked-point distance |B−A| (NOT the face minimum). */
  distance: number;
  /** Signed dX, dY, dZ from A to B. */
  delta: [number, number, number];
  /** Signed separation of B along face A's normal. */
  alongNormalA: number;
  /** Remaining offset within face A's tangent plane. */
  inPlane: number;
  /** Signed separation along face B's normal (still measured A→B). */
  alongNormalB: number;
  /** Remaining offset within face B's tangent plane. */
  inPlaneB: number;
  /** Directed angle between the two normals, 0–180°. */
  normalAngleDeg: number;
  /** Orientation-independent plane angle, 0–90°. */
  planeAngleDeg: number;
}

type Vec3 = [number, number, number];

function normalize(v: Vec3): Vec3 {
  const len = Math.hypot(v[0], v[1], v[2]);
  if (len < 1e-12) return [0, 0, 1];
  return [v[0] / len, v[1] / len, v[2] / len];
}

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const clamp = (v: number, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, v));
const DEG = 180 / Math.PI;

export function computeMeasurement(
  a: MeasurePick, b: MeasurePick,
): MeasureReadout {
  const delta: Vec3 = [
    b.point[0] - a.point[0],
    b.point[1] - a.point[1],
    b.point[2] - a.point[2],
  ];
  const distance = Math.hypot(delta[0], delta[1], delta[2]);
  const nA = normalize(a.normal);
  const nB = normalize(b.normal);
  const alongNormalA = dot(delta, nA);
  const alongNormalB = dot(delta, nB);
  const inPlaneOf = (along: number) => Math.sqrt(
    Math.max(0, distance * distance - along * along));
  const cos = clamp(dot(nA, nB), -1, 1);
  return {
    distance,
    delta,
    alongNormalA,
    inPlane: inPlaneOf(alongNormalA),
    alongNormalB,
    inPlaneB: inPlaneOf(alongNormalB),
    normalAngleDeg: Math.acos(cos) * DEG,
    planeAngleDeg: Math.acos(Math.min(1, Math.abs(cos))) * DEG,
  };
}
