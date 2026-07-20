// Client-side candidate-direction generation. The direction set is a live,
// in-browser model — changing the setup regenerates the arrows instantly with
// zero backend cost. Accessibility (the expensive per-direction visibility
// compute) is a separate concern owned by a dedicated view; nothing here
// touches it.
//
// Directions are oriented: an axis and its opposite are two distinct
// candidates (both mold halves / both setups). World, PCA and hole axes are
// bidirectional lines, so they emit BOTH ± ends; manual vectors and surface
// normals are directed and emit one. Collinear candidates (same direction)
// merge, carrying every provenance that produced them.

import type { HoleCandidate } from '../../api/types';

export type SourceKind =
  | 'uniform' | 'principal_axis' | 'bbox_axis' | 'hole_axis'
  | 'face_normal' | 'average_normal' | 'manual';

export interface Provenance {
  source: SourceKind;
  label: string;
  detail: Record<string, any>;
}

export interface GeneratedDir {
  vector: [number, number, number];
  provenances: Provenance[];
  /** sign-sensitive direction key — identity for dedup, suppression, delete. */
  key: string;
}

export interface DirectionSetup {
  count: number;            // uniform sphere samples (default 0)
  axes: boolean;            // world ±X/±Y/±Z
  bboxAxes: boolean;        // PCA / oriented bounding-box axes (±)
  holeAxes: boolean;        // analytic hole / cylinder axes (±)
  manual: number[][];       // explicit directed vectors
  brepGroups: number[][];   // groups of BREP face ids → averaged normal
  suppressed: string[];     // direction keys removed from the view
}

export interface DirGeometry {
  verts: Float32Array;
  faces: Uint32Array;       // vertex indices (faceCount * 3)
  normals: Float32Array;    // per-face unit normals (faceCount * 3)
  faceCount: number;
  brepIds: Uint32Array | null;   // per-facet BREP face id (null if unmeshed)
  holeCandidates: HoleCandidate[];
}

export const EMPTY_SETUP: DirectionSetup = {
  count: 0, axes: false, bboxAxes: false, holeAxes: false,
  manual: [], brepGroups: [], suppressed: [],
};

type Vec3 = [number, number, number];

function unit(v: number[]): Vec3 {
  const n = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / n, v[1] / n, v[2] / n];
}

/** Sign-sensitive direction key: the rounded unit vector. +Z and −Z differ. */
export function dirKey(v: number[]): string {
  const [x, y, z] = unit(v);
  const r = (a: number) => (Math.round(a * 1e4) / 1e4).toFixed(4);
  return `${r(x)},${r(y)},${r(z)}`;
}

/** N directions spread over the sphere (Fibonacci / golden spiral). */
function goldenSpiral(n: number): Vec3[] {
  if (n <= 0) return [];
  if (n === 1) return [[0, 1, 0]];
  const phi = Math.PI * (3 - Math.sqrt(5));
  const out: Vec3[] = [];
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / (n - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = phi * i;
    out.push([Math.cos(theta) * r, y, Math.sin(theta) * r]);
  }
  return out;
}

// eigenvectors of a symmetric 3×3 by cyclic Jacobi (columns of V), descending
function jacobiEig(a: number[][]): { val: number; vec: Vec3 }[] {
  const c = a.map((row) => row.slice());
  const V = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  for (let sweep = 0; sweep < 16; sweep++) {
    let off = 0;
    for (let p = 0; p < 3; p++) for (let q = p + 1; q < 3; q++) off += c[p][q] * c[p][q];
    if (off < 1e-20) break;
    for (let p = 0; p < 3; p++) for (let q = p + 1; q < 3; q++) {
      if (Math.abs(c[p][q]) < 1e-20) continue;
      const theta = (c[q][q] - c[p][p]) / (2 * c[p][q]);
      const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
      const cs = 1 / Math.sqrt(t * t + 1);
      const sn = t * cs;
      for (let k = 0; k < 3; k++) {
        const ckp = c[k][p], ckq = c[k][q];
        c[k][p] = cs * ckp - sn * ckq; c[k][q] = sn * ckp + cs * ckq;
      }
      for (let k = 0; k < 3; k++) {
        const cpk = c[p][k], cqk = c[q][k];
        c[p][k] = cs * cpk - sn * cqk; c[q][k] = sn * cpk + cs * cqk;
      }
      for (let k = 0; k < 3; k++) {
        const vkp = V[k][p], vkq = V[k][q];
        V[k][p] = cs * vkp - sn * vkq; V[k][q] = sn * vkp + cs * vkq;
      }
    }
  }
  return [0, 1, 2]
    .map((i) => ({ val: c[i][i], vec: unit([V[0][i], V[1][i], V[2][i]]) }))
    .sort((p, q) => q.val - p.val);
}

const pcaCache = new WeakMap<Float32Array, Vec3[]>();

/** Three orthonormal principal axes of the SURFACE (area-weighted covariance
 * of face centroids) — tessellation-independent, so an axis-aligned part's
 * OBB lines up with the world axes. Major axis first. Cached per geometry. */
export function pcaAxes(verts: Float32Array, faces: Uint32Array): Vec3[] {
  const hit = pcaCache.get(verts);
  if (hit) return hit;
  const F = faces.length / 3;
  if (F < 1) return [];
  const cents = new Float64Array(F * 3);
  const areas = new Float64Array(F);
  let W = 0, mx = 0, my = 0, mz = 0;
  for (let f = 0; f < F; f++) {
    const a = faces[3 * f] * 3, b = faces[3 * f + 1] * 3, c = faces[3 * f + 2] * 3;
    const ax = verts[a], ay = verts[a + 1], az = verts[a + 2];
    const e1x = verts[b] - ax, e1y = verts[b + 1] - ay, e1z = verts[b + 2] - az;
    const e2x = verts[c] - ax, e2y = verts[c + 1] - ay, e2z = verts[c + 2] - az;
    const cx = e1y * e2z - e1z * e2y, cy = e1z * e2x - e1x * e2z, cz = e1x * e2y - e1y * e2x;
    const area = 0.5 * Math.hypot(cx, cy, cz);
    const gx = (ax + verts[b] + verts[c]) / 3;
    const gy = (ay + verts[b + 1] + verts[c + 1]) / 3;
    const gz = (az + verts[b + 2] + verts[c + 2]) / 3;
    cents[3 * f] = gx; cents[3 * f + 1] = gy; cents[3 * f + 2] = gz; areas[f] = area;
    W += area; mx += area * gx; my += area * gy; mz += area * gz;
  }
  if (W <= 0) return [];
  mx /= W; my /= W; mz /= W;
  const cov = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (let f = 0; f < F; f++) {
    const w = areas[f];
    const dx = cents[3 * f] - mx, dy = cents[3 * f + 1] - my, dz = cents[3 * f + 2] - mz;
    cov[0][0] += w * dx * dx; cov[0][1] += w * dx * dy; cov[0][2] += w * dx * dz;
    cov[1][1] += w * dy * dy; cov[1][2] += w * dy * dz; cov[2][2] += w * dz * dz;
  }
  cov[1][0] = cov[0][1]; cov[2][0] = cov[0][2]; cov[2][1] = cov[1][2];
  const axes = jacobiEig(cov).map((e) => e.vec);
  pcaCache.set(verts, axes);
  return axes;
}

/** Mean outward normal over every facet belonging to a set of BREP faces. */
export function averageBrepNormal(geom: DirGeometry, brepFaceIds: number[]): Vec3 | null {
  if (!geom.brepIds) return null;
  const want = new Set(brepFaceIds);
  let x = 0, y = 0, z = 0, n = 0;
  for (let f = 0; f < geom.faceCount; f++) {
    if (!want.has(geom.brepIds[f])) continue;
    x += geom.normals[3 * f]; y += geom.normals[3 * f + 1]; z += geom.normals[3 * f + 2];
    n++;
  }
  if (!n) return null;
  return unit([x, y, z]);
}

/** BREP face ids a direction was built from (hole / surface-normal sources). */
export function brepFacesOf(dir: GeneratedDir): number[] {
  const s = new Set<number>();
  for (const p of dir.provenances) {
    for (const b of (p.detail?.brep_faces ?? [])) s.add(b);
  }
  return [...s];
}

/** Build the live candidate set from the setup + geometry. */
export function buildDirections(setup: DirectionSetup, geom: DirGeometry): GeneratedDir[] {
  const merged = new Map<string, GeneratedDir>();
  const suppressed = new Set(setup.suppressed);

  const add = (v: number[], prov: Provenance) => {
    const key = dirKey(v);
    if (suppressed.has(key)) return;
    const existing = merged.get(key);
    if (existing) {
      if (!existing.provenances.some((p) => p.source === prov.source && p.label === prov.label)) {
        existing.provenances.push(prov);
      }
    } else {
      merged.set(key, { vector: unit(v), provenances: [prov], key });
    }
  };
  // a bidirectional axis (line): emit both ends
  const addAxis = (v: Vec3, source: SourceKind, label: string, detail: Record<string, any>) => {
    add(v, { source, label: `+${label}`, detail });
    add([-v[0], -v[1], -v[2]], { source, label: `−${label}`, detail });
  };

  if (setup.axes) {
    addAxis([1, 0, 0], 'principal_axis', 'X', { axis: 'X' });
    addAxis([0, 1, 0], 'principal_axis', 'Y', { axis: 'Y' });
    addAxis([0, 0, 1], 'principal_axis', 'Z', { axis: 'Z' });
  }
  goldenSpiral(Math.max(0, Math.floor(setup.count))).forEach((v, i) =>
    add(v, { source: 'uniform', label: `uniform ${i}`, detail: {} }));
  if (setup.bboxAxes) {
    pcaAxes(geom.verts, geom.faces).forEach((v, i) =>
      addAxis(v, 'bbox_axis', `PCA ${i + 1}`, { axis: i }));
  }
  if (setup.holeAxes) {
    geom.holeCandidates.forEach((h) => {
      const r = h.detail?.radius;
      const label = r ? `hole ø${(2 * r).toFixed(1)}` : 'hole';
      addAxis(unit(h.axis), 'hole_axis', label, h.detail);
    });
  }
  setup.manual.forEach((v) => add(v, {
    source: 'manual', label: `[${unit(v).map((c) => c.toFixed(2)).join(', ')}]`,
    detail: { vector: unit(v) },
  }));
  setup.brepGroups.forEach((group, gi) => {
    const v = averageBrepNormal(geom, group);
    if (!v) return;
    const single = group.length === 1;
    add(v, {
      source: single ? 'face_normal' : 'average_normal',
      label: single ? `BREP face ${group[0]}` : `${group.length} BREP faces`,
      detail: { brep_faces: group, group: gi },
    });
  });

  return [...merged.values()];
}

// The most recently generated set + its facet→BREP id map, stashed so the
// arrow tooltip and the plugin's click handler can resolve an arrow/facet
// without recomputing. Set during the mode's paint().
export let currentDirections: GeneratedDir[] = [];
export let currentBrepIds: Uint32Array | null = null;
export function setCurrentDirections(dirs: GeneratedDir[]) { currentDirections = dirs; }
export function setCurrentBrepIds(ids: Uint32Array | null) { currentBrepIds = ids; }
