// Pure section-snap math — no three.js, unit-testable. Given a picked point
// and the picked BREP face's analytic surface (brep_meta.json entry), derive
// the section plane an engineer most likely wants:
//   plane    → the section coincides with the face's plane
//   cylinder / cone / torus → a plane THROUGH the axis/centerline, oriented
//              as close to the current view as the axis allows
//   sphere   → the current plane translated through the center
//   freeform / unknown → the current plane translated through the pick
// The plane snap keeps `flip` semantics out: callers reset flip so "cut away
// what the normal points at" stays predictable after a snap.

export interface SurfaceParams {
  type: 'plane' | 'cylinder' | 'cone' | 'sphere' | 'torus';
  normal?: [number, number, number]; // plane (no anchor point — use the pick)
  point?: [number, number, number]; // cylinder axis anchor
  axis?: [number, number, number]; // cylinder / cone / torus
  apex?: [number, number, number]; // cone
  center?: [number, number, number]; // sphere / torus
}

export interface SnapResult {
  normal: [number, number, number];
  offset: number;
}

type Vec3 = [number, number, number];

const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

function normalize(v: Vec3): Vec3 | null {
  const len = Math.hypot(v[0], v[1], v[2]);
  if (len < 1e-12) return null;
  return [v[0] / len, v[1] / len, v[2] / len];
}

/** Any unit vector perpendicular to `axis`. */
function anyPerpendicular(axis: Vec3): Vec3 {
  const seed: Vec3 = Math.abs(axis[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
  return perpendicularComponent(seed, axis)!;
}

/** Unit component of `v` perpendicular to unit `axis` (null if parallel). */
function perpendicularComponent(v: Vec3, axis: Vec3): Vec3 | null {
  const along = dot(v, axis);
  return normalize([
    v[0] - along * axis[0],
    v[1] - along * axis[1],
    v[2] - along * axis[2],
  ]);
}

/**
 * @param surface   analytic surface of the picked BREP face (null = freeform/STL)
 * @param pickPoint posed world position of the pick (plane/fallback anchor)
 * @param viewDir   camera view direction — orients axis-snapped planes
 * @param current   the section plane's current normal — fallback orientation
 */
export function snapSection(
  surface: SurfaceParams | null,
  pickPoint: Vec3,
  viewDir: Vec3,
  current: Vec3,
): SnapResult {
  const fallback = (): SnapResult => {
    const n = normalize(current) ?? [1, 0, 0];
    return { normal: n, offset: dot(n, pickPoint) };
  };
  if (!surface) return fallback();

  if (surface.type === 'plane' && surface.normal) {
    const n = normalize(surface.normal);
    if (!n) return fallback();
    // the meta records no anchor point — the picked point lies on the face
    return { normal: n, offset: dot(n, pickPoint) };
  }

  if ((surface.type === 'cylinder' || surface.type === 'cone'
    || surface.type === 'torus') && surface.axis) {
    const axis = normalize(surface.axis);
    const anchor = surface.point ?? surface.apex ?? surface.center;
    if (!axis || !anchor) return fallback();
    // a plane CONTAINING the centerline: normal ⟂ axis, facing the camera
    // as much as the axis allows (stable, view-relevant cross-section)
    const n = perpendicularComponent(normalize(viewDir) ?? [0, 0, 1], axis)
      ?? anyPerpendicular(axis);
    return { normal: n, offset: dot(n, anchor) };
  }

  if (surface.type === 'sphere' && surface.center) {
    const n = normalize(current) ?? [1, 0, 0];
    return { normal: n, offset: dot(n, surface.center) };
  }

  return fallback();
}

/** The corner of the picked triangle nearest to the hit point — an exact
 * vertex anchor for freeform snaps. */
export function nearestCorner(
  verts: Float32Array, faces: Uint32Array, face: number, hit: Vec3,
): Vec3 {
  let best: Vec3 = hit;
  let bestDist = Infinity;
  for (let k = 0; k < 3; k++) {
    const v = faces[3 * face + k];
    const p: Vec3 = [verts[3 * v], verts[3 * v + 1], verts[3 * v + 2]];
    const d = (p[0] - hit[0]) ** 2 + (p[1] - hit[1]) ** 2 + (p[2] - hit[2]) ** 2;
    if (d < bestDist) {
      bestDist = d;
      best = p;
    }
  }
  return best;
}
