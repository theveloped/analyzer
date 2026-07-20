// The directions view: candidate approach directions drawn as arrows, built
// live in the browser from the setup (no backend round-trip), colored by
// provenance. BREP faces being picked (for an averaged normal) or inspected
// highlight on the mesh.

import type { PaintInfo, RGB, ViewCtx, ViewMode } from '../../registry/types';
import type { SourceKind } from './build';
import {
  buildDirections, EMPTY_SETUP, setCurrentBrepIds, setCurrentDirections,
} from './build';

export const PROVENANCE_COLORS: Record<SourceKind, RGB> = {
  uniform: [0.60, 0.62, 0.66],
  principal_axis: [0.85, 0.25, 0.25],
  bbox_axis: [0.95, 0.55, 0.15],
  hole_axis: [0.15, 0.70, 0.82],
  face_normal: [0.25, 0.70, 0.38],
  average_normal: [0.25, 0.70, 0.38],
  manual: [0.80, 0.30, 0.75],
};

export const PROVENANCE_LABELS: Record<SourceKind, string> = {
  uniform: 'uniform sample',
  principal_axis: 'principal axis',
  bbox_axis: 'bounding-box (PCA) axis',
  hole_axis: 'hole / cylinder axis',
  face_normal: 'BREP face normal',
  average_normal: 'averaged face normal',
  manual: 'manual axis',
};

const BASE: RGB = [0.85, 0.88, 0.90];
const PICKED: RGB = [0.98, 0.85, 0.20];   // BREP faces being picked
const HILITE: RGB = [0.20, 0.70, 0.95];   // BREP faces of an inspected direction

// per-part cache of the facet→BREP-id map (loaded once from the manifest)
let brepIdsPart: string | null = null;
let brepIdsCache: Uint32Array | null = null;

async function loadBrepIds(ctx: ViewCtx): Promise<Uint32Array | null> {
  const partId = ctx.manifest.part.id;
  if (brepIdsPart === partId) return brepIdsCache;
  const desc = ctx.manifest.fields.find((f) => f.id === 'brep_faces');
  brepIdsCache = desc ? (await ctx.getField(desc) as Uint32Array) : null;
  brepIdsPart = partId;
  return brepIdsCache;
}

function meshBounds(ctx: ViewCtx): { center: [number, number, number]; radius: number } {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  const v = ctx.verts;
  for (let i = 0; i < v.length; i += 3) {
    for (let a = 0; a < 3; a++) {
      if (v[i + a] < min[a]) min[a] = v[i + a];
      if (v[i + a] > max[a]) max[a] = v[i + a];
    }
  }
  const dx = max[0] - min[0], dy = max[1] - min[1], dz = max[2] - min[2];
  return {
    center: [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2],
    radius: Math.sqrt(dx * dx + dy * dy + dz * dz) / 2,
  };
}

/** Merge the stored setup fields with defaults so a bare params bag works. */
export function setupFrom(params: Record<string, any>) {
  return {
    count: params.count ?? EMPTY_SETUP.count,
    axes: params.axes ?? EMPTY_SETUP.axes,
    bboxAxes: params.bboxAxes ?? EMPTY_SETUP.bboxAxes,
    holeAxes: params.holeAxes ?? EMPTY_SETUP.holeAxes,
    manual: params.manual ?? EMPTY_SETUP.manual,
    brepGroups: params.brepGroups ?? EMPTY_SETUP.brepGroups,
    suppressed: params.suppressed ?? EMPTY_SETUP.suppressed,
  };
}

export const directionsMode: ViewMode = {
  id: 'directions',
  label: 'Candidate directions',
  async paint(ctx): Promise<PaintInfo> {
    const brepIds = await loadBrepIds(ctx);
    setCurrentBrepIds(brepIds);

    const dirs = buildDirections(setupFrom(ctx.params), {
      verts: ctx.verts, faces: ctx.faces, normals: ctx.normals,
      faceCount: ctx.faceCount, brepIds,
      holeCandidates: ctx.manifest.hole_candidates ?? [],
    });
    setCurrentDirections(dirs);

    // face highlighting: yellow = faces being picked, blue = faces behind the
    // direction under inspection (both are BREP-face id sets → facet masks)
    const pickBrep = new Set<number>(ctx.params.pickMode ? (ctx.params.pendingBrep ?? []) : []);
    const hiBrep = new Set<number>(ctx.params.highlightBrep ?? []);
    ctx.paintFaces((f) => {
      const b = brepIds ? brepIds[f] : -1;
      if (pickBrep.has(b)) return PICKED;
      if (hiBrep.has(b)) return HILITE;
      return BASE;
    });

    ctx.setArrows(dirs.map((d) => ({
      direction: d.vector,
      color: PROVENANCE_COLORS[d.provenances[0].source] ?? PROVENANCE_COLORS.uniform,
    })));

    // legend: one row per primary provenance present, click flies along it
    const { center, radius } = meshBounds(ctx);
    const groups = new Map<SourceKind, { n: number; rep: number[] }>();
    for (const d of dirs) {
      const src = d.provenances[0].source;
      const g = groups.get(src);
      if (g) g.n++; else groups.set(src, { n: 1, rep: d.vector });
    }
    const legend = [...groups.entries()].map(([src, g]) => ({
      color: PROVENANCE_COLORS[src],
      label: `${PROVENANCE_LABELS[src]} (${g.n})`,
      focus: { center, direction: g.rep as [number, number, number], radius },
    }));

    const picking = !!ctx.params.pickMode;
    const stats = `${dirs.length} candidate direction${dirs.length === 1 ? '' : 's'}`
      + ` from ${groups.size} source${groups.size === 1 ? '' : 's'}`
      + (picking ? ` · ${pickBrep.size} BREP face${pickBrep.size === 1 ? '' : 's'} picked` : '')
      + (dirs.length ? '' : '\nadd sources in the panel — arrows update live');
    return { legend, stats };
  },
};
