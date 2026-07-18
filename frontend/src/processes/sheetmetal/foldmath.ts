// Fold kinematics for the bend-sequence animation: a line-for-line port of
// pressbrake/foldmesh.py (pose_vertices) and kinematics.fold_transforms /
// bend_deduction. Keep the two in sync — the Python side is the source of
// truth and is pinned by test_bendplan.py's round-trip fixtures.
//
// Matrices are row-major Float64Array(16); points are [x, y, z].

export interface BendStats {
  id: number;
  axis_point: [number, number];
  axis_dir: [number, number];
  angle_target: number;
  angle_overbend: number;
  angle_relaxed: number;
  inner_radius: number;
  k_factor: number;
  zone_width: number;
  zone_shift: number;
  parent_panel: number;
  child_panel: number;
}

export interface GraphStats {
  thickness: number;
  z_offset: number;
  base_panel: number;
  panels: { id: number }[];
  bends: BendStats[];
}

export type Mat4 = Float64Array;

const HEM_CAP = (150.0 * Math.PI) / 180.0;

export function identity(): Mat4 {
  const m = new Float64Array(16);
  m[0] = 1; m[5] = 1; m[10] = 1; m[15] = 1;
  return m;
}

export function matMul(a: Mat4, b: Mat4): Mat4 {
  const out = new Float64Array(16);
  for (let i = 0; i < 4; i++) {
    for (let j = 0; j < 4; j++) {
      let sum = 0;
      for (let k = 0; k < 4; k++) sum += a[4 * i + k] * b[4 * k + j];
      out[4 * i + j] = sum;
    }
  }
  return out;
}

/** Rodrigues rotation about the 3D line through (point2 at height z) with
 * in-plane direction direction2 — kinematics.rotation_about_line. */
export function rotationAboutLine(
  point2: [number, number], direction2: [number, number],
  angle: number, z = 0,
): Mat4 {
  const [kx, ky] = direction2;
  const kz = 0;
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  const t = 1 - c;
  const r = [
    c + t * kx * kx, t * kx * ky - s * kz, t * kx * kz + s * ky,
    t * kx * ky + s * kz, c + t * ky * ky, t * ky * kz - s * kx,
    t * kx * kz - s * ky, t * ky * kz + s * kx, c + t * kz * kz,
  ];
  const p = [point2[0], point2[1], z];
  const m = identity();
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) m[4 * i + j] = r[3 * i + j];
    m[4 * i + 3] = p[i] - (r[3 * i] * p[0] + r[3 * i + 1] * p[1]
      + r[3 * i + 2] * p[2]);
  }
  return m;
}

export function rotationX(angle: number): Mat4 {
  return rotationAboutLine([0, 0], [1, 0], angle, 0);
}

export function bendDeduction(
  graph: GraphStats, bend: BendStats, angle: number,
): number {
  if (bend.zone_width <= 0) return 0;
  const magnitude = Math.abs(angle);
  if (magnitude < 1e-9) return 0;
  const target = Math.max(Math.abs(bend.angle_target), 1e-9);
  const theta = Math.min(magnitude, HEM_CAP);
  const midRadius = bend.inner_radius + graph.thickness / 2;
  const consumed = bend.zone_width * Math.min(magnitude / target, 1);
  return Math.max(0, 2 * midRadius * Math.tan(theta / 2) - consumed);
}

function normal2d(direction: [number, number]): [number, number] {
  return [-direction[1], direction[0]];
}

/** Per-panel transforms for hinge angles theta — fold_transforms, with an
 * optional premultiplied machine transform baked into every panel. */
export function foldTransforms(
  graph: GraphStats, theta: Float64Array | number[], premultiply?: Mat4,
): Mat4[] {
  const transforms: Mat4[] = graph.panels.map(() => identity());
  const resolved = new Set<number>([graph.base_panel]);
  let pending = [...graph.bends];
  while (pending.length) {
    const remaining: BendStats[] = [];
    let progressed = false;
    for (const bend of pending) {
      if (!resolved.has(bend.parent_panel)) {
        remaining.push(bend);
        continue;
      }
      let hinge = rotationAboutLine(
        bend.axis_point, bend.axis_dir, theta[bend.id] as number,
        graph.z_offset);
      const deduction = bendDeduction(
        graph, bend, theta[bend.id] as number);
      if (deduction) {
        const slide = identity();
        const n = normal2d(bend.axis_dir);
        slide[3] = deduction * n[0];
        slide[7] = deduction * n[1];
        hinge = matMul(hinge, slide);
      }
      transforms[bend.child_panel] = matMul(
        transforms[bend.parent_panel], hinge);
      resolved.add(bend.child_panel);
      progressed = true;
    }
    if (!progressed) throw new Error('bend graph is not a tree');
    pending = remaining;
  }
  if (premultiply) {
    for (let p = 0; p < transforms.length; p++) {
      transforms[p] = matMul(premultiply, transforms[p]);
    }
  }
  return transforms;
}

interface BendPose {
  parent: Mat4;
  child: Mat4;
  axisPoint: [number, number];
  axisDir: [number, number];
  normal: [number, number];
  eParent: number;
  u0: number;
  consumed: number;
  radiusEff: number;
  midRadius: number;
  s: number;
  magnitude: number;
  flatOnly: boolean;
}

function bendPose(
  graph: GraphStats, bend: BendStats, transforms: Mat4[], theta: number,
): BendPose {
  const magnitude = Math.abs(theta);
  const zone = bend.zone_width;
  const eParent = zone / 2 + bend.zone_shift;
  const flatOnly = magnitude < 1e-9 || zone <= 0;
  const target = Math.max(Math.abs(bend.angle_target), 1e-9);
  const consumed = flatOnly ? 0
    : zone * Math.min(magnitude / target, 1);
  const midRadius = bend.inner_radius + graph.thickness / 2;
  const setback = midRadius * Math.tan(Math.min(magnitude, HEM_CAP) / 2);
  const u0 = flatOnly ? 0
    : Math.min(Math.max(eParent - setback, 0), Math.max(zone - consumed, 0));
  return {
    parent: transforms[bend.parent_panel],
    child: transforms[bend.child_panel],
    axisPoint: bend.axis_point,
    axisDir: bend.axis_dir,
    normal: normal2d(bend.axis_dir),
    eParent,
    u0,
    consumed,
    radiusEff: flatOnly ? 1 : consumed / magnitude,
    midRadius,
    s: theta >= 0 ? 1 : -1,
    magnitude,
    flatOnly,
  };
}

function applyInto(
  m: Mat4, x: number, y: number, z: number,
  out: Float32Array, offset: number,
) {
  out[offset] = m[0] * x + m[1] * y + m[2] * z + m[3];
  out[offset + 1] = m[4] * x + m[5] * y + m[6] * z + m[7];
  out[offset + 2] = m[8] * x + m[9] * y + m[10] * z + m[11];
}

/** pose_vertices: fold flat coordinates to hinge angles theta. flat is the
 * stored f4 array (3V), vertexPanel/vertexBend the +1-encoded owners.
 * Writes into (and returns) `out`. */
export function poseVertices(
  graph: GraphStats, flat: Float32Array,
  vertexPanel: Uint8Array, vertexBend: Uint8Array,
  theta: Float64Array | number[], premultiply: Mat4 | undefined,
  out: Float32Array,
): Float32Array {
  const transforms = foldTransforms(graph, theta, premultiply);
  const bendPoses = graph.bends.map(
    (bend) => bendPose(graph, bend, transforms, theta[bend.id] as number));
  const zOffset = graph.z_offset;
  const count = flat.length / 3;
  for (let v = 0; v < count; v++) {
    const x = flat[3 * v];
    const y = flat[3 * v + 1];
    const z = flat[3 * v + 2];
    const bendOwner = vertexBend[v];
    if (!bendOwner) {
      const panel = vertexPanel[v];
      const m = panel ? transforms[panel - 1]
        : (premultiply ?? identity());
      applyInto(m, x, y, z, out, 3 * v);
      continue;
    }
    const pose = bendPoses[bendOwner - 1];
    if (pose.flatOnly) {
      applyInto(pose.parent, x, y, z, out, 3 * v);
      continue;
    }
    const relX = x - pose.axisPoint[0];
    const relY = y - pose.axisPoint[1];
    const d = relX * pose.normal[0] + relY * pose.normal[1];
    const a = relX * pose.axisDir[0] + relY * pose.axisDir[1];
    const zeta = z - zOffset;
    const u = d + pose.eParent;
    if (u <= pose.u0) {
      applyInto(pose.parent, x, y, z, out, 3 * v);
      continue;
    }
    if (u >= pose.u0 + pose.consumed) {
      applyInto(pose.child, x, y, z, out, 3 * v);
      continue;
    }
    const rho = pose.midRadius - pose.s * zeta;
    const phiV = (u - pose.u0) / pose.radiusEff;
    const dNew = (pose.u0 - pose.eParent) + rho * Math.sin(phiV);
    const zNew = zOffset + pose.s * (pose.midRadius - rho * Math.cos(phiV));
    const ax = pose.axisPoint[0] + a * pose.axisDir[0] + dNew * pose.normal[0];
    const ay = pose.axisPoint[1] + a * pose.axisDir[1] + dNew * pose.normal[1];
    applyInto(pose.parent, ax, ay, zNew, out, 3 * v);

    // exact child-edge closure (see foldmesh.py): blend the overbend
    // residual between the ideal arc end and the child-rigid region
    const endD = (pose.u0 + pose.consumed) - pose.eParent;
    const endArcD = (pose.u0 - pose.eParent) + rho * Math.sin(pose.magnitude);
    const endArcZ = zOffset
      + pose.s * (pose.midRadius - rho * Math.cos(pose.magnitude));
    const bx = pose.axisPoint[0] + a * pose.axisDir[0]
      + endArcD * pose.normal[0];
    const by = pose.axisPoint[1] + a * pose.axisDir[1]
      + endArcD * pose.normal[1];
    const cx = pose.axisPoint[0] + a * pose.axisDir[0] + endD * pose.normal[0];
    const cy = pose.axisPoint[1] + a * pose.axisDir[1] + endD * pose.normal[1];
    const scratch = poseScratch;
    applyInto(pose.child, cx, cy, zOffset + zeta, scratch, 0);
    applyInto(pose.parent, bx, by, endArcZ, scratch, 3);
    const w = phiV / pose.magnitude;
    out[3 * v] += w * (scratch[0] - scratch[3]);
    out[3 * v + 1] += w * (scratch[1] - scratch[4]);
    out[3 * v + 2] += w * (scratch[2] - scratch[5]);
  }
  return out;
}

const poseScratch = new Float32Array(6);

/** Vertical descent of the bend line into the V die at stroke phi — the
 * wings pivot on the die shoulder lines, so the part sinks as they
 * incline. Mirror of foldmesh.stroke_descent. */
export function strokeDescent(
  thickness: number, vWidth: number | null | undefined, phi: number,
): number {
  if (!vWidth) return 0;
  const halfAngle = Math.min(Math.abs(phi), HEM_CAP) / 2;
  return (vWidth / 2) * Math.tan(halfAngle)
    - (thickness / 2) / Math.cos(halfAngle) + thickness / 2;
}

/** The machine pose premultiply of one plan step at stroke phi:
 * T_z(-descent) @ R_x(lift_sign * phi / 2) @ placement. */
export function machinePremultiply(
  placement: number[], liftSign: number, phi: number, descent = 0,
): Mat4 {
  const m = matMul(rotationX(liftSign * phi / 2),
    Float64Array.from(placement));
  m[11] -= descent; // row-major: the z translation
  return m;
}
