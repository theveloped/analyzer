// Turning views over the cnc/turning result: per-face turning roles with the
// lathe section drawn through the part, and the revolution-error heatmap that
// answers "why isn't this face turned".

import type { Manifest, ResultEntry } from '../../api/types';
import { COL, FocusTracker, rampColor } from '../../colorizers/core';
import { covered, type CoverageRule } from './coverage';
import { sequentialGradientCss } from '../../viewer/colormaps';
import type {
  LegendEntry, RGB, ViewCtx, ViewMode,
} from '../../registry/types';
import {
  drawSplitOverlays, effectiveDescriptor, type SplitHost,
} from '../../splits/splits';
import { SplitControls } from '../../splits/SplitControls';

// keep in sync with TURNING_SCHEMA in processes/cnc.py
export const TURNING_SCHEMA = 2;

// index == backend category code (turning.TURN_ROLES)
const ROLE_LABELS = ['milled / other', 'OD facing', 'OD turning',
                     'ID facing', 'ID turning', 'on axis'];
// external operations blue-ish, internal ones red/orange-ish — the convention
// the Analysis Situs turning recognizer uses, so the split reads at a glance
const ROLE_COLORS: RGB[] = [
  COL.inaccess,          // milled / other
  [0.55, 0.75, 0.93],    // OD facing
  [0.30, 0.55, 0.85],    // OD turning
  [0.95, 0.63, 0.35],    // ID facing
  [0.88, 0.35, 0.28],    // ID turning
  [0.60, 0.60, 0.66],    // on axis
];
const SECTION_COLOR: RGB = [1.0, 0.85, 0.25];
const INNER_COLOR: RGB = [1.0, 0.55, 0.35];
// brep_default sentinel — keep in sync with turning.CONFLICT_ROLE
const CONFLICT_ROLE = 254;
const CONFLICT_COLOR: RGB = [0.95, 0.25, 0.75];
const AXIS_COLOR: RGB = [0.55, 0.85, 0.55];

type Vec3 = [number, number, number];

/** Usable turning results, newest last. */
export function turningResults(manifest: Manifest): ResultEntry[] {
  return manifest.results.filter((r) => r.process === 'cnc'
    && r.analysis === 'turning' && !r.stale
    && r.params.schema === TURNING_SCHEMA);
}

export function latestTurning(ctx: ViewCtx): ResultEntry | null {
  const results = turningResults(ctx.manifest);
  return results.length ? results[results.length - 1] : null;
}

async function turningField(ctx: ViewCtx, result: ResultEntry, name: string) {
  const desc = ctx.manifest.fields.find(
    (f) => f.id === `results.cnc.turning.${result.hash}.${name}`);
  if (!desc) throw new Error(`turning field "${name}" missing — re-run cnc/turning`);
  return ctx.getField(desc);
}

function require(ctx: ViewCtx): ResultEntry {
  const result = latestTurning(ctx);
  if (!result) {
    throw new Error('no turning result — run cnc/turning in the Compute panel');
  }
  return result;
}

function summary(result: ResultEntry): string {
  const s = result.stats;
  const head = `${s.verdict} · Ø${Number(s.max_diameter).toFixed(1)} × `
    + `${Number(s.length).toFixed(1)} mm`;
  const shares = `${(100 * s.turned_area_fraction).toFixed(0)}% turned `
    + `(${(100 * s.radial_area_fraction).toFixed(0)}% swept), `
    + `${(100 * s.milled_area_fraction).toFixed(0)}% milled`;
  return `${head} · ${shares}`;
}

/** Any unit vector perpendicular to `d` — the section's radial reference. */
function perpendicular(d: Vec3): Vec3 {
  const other: Vec3 = Math.abs(d[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
  const v: Vec3 = [
    d[1] * other[2] - d[2] * other[1],
    d[2] * other[0] - d[0] * other[2],
    d[0] * other[1] - d[1] * other[0],
  ];
  const n = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / n, v[1] / n, v[2] / n];
}

/**
 * The stored (z, r) meridian lifted into world space.
 *
 * Profile coordinates are AXIAL — z from stats.axis.point along
 * stats.axis.direction — so a point is `p + z*d + r*u`. Both halves (+u and
 * -u) are drawn so the result reads as a lathe section through the part.
 *
 * The inner contour is a separate polyline, not part of the outer one: the
 * meridian of a bored part is a region with holes rather than a single closed
 * curve. Without it the section shows only the outer silhouette and every
 * internal feature is missing from it.
 */
function sectionLines(result: ResultEntry): {
  outer: Float32Array; inner: Float32Array; axis: Float32Array;
} {
  const s = result.stats;
  const p = s.axis.point as Vec3;
  const d = s.axis.direction as Vec3;
  const u = perpendicular(d);

  const at = (z: number, r: number, sign: number): Vec3 => [
    p[0] + z * d[0] + sign * r * u[0],
    p[1] + z * d[1] + sign * r * u[1],
    p[2] + z * d[2] + sign * r * u[2],
  ];

  const mirrored = (profile: [number, number][]) => {
    const segments: number[] = [];
    for (const sign of [1, -1]) {
      for (let i = 0; i + 1 < profile.length; i++) {
        segments.push(...at(profile[i][0], profile[i][1], sign),
                      ...at(profile[i + 1][0], profile[i + 1][1], sign));
      }
    }
    return new Float32Array(segments);
  };

  const profile = (s.profile ?? []) as [number, number][];
  const zLo = profile.length ? profile[0][0] : 0;
  const zHi = profile.length ? profile[profile.length - 1][0] : 0;
  // one polyline per contiguous bored run, concatenated into a single line
  // buffer: a part bored from both ends has solid material between, and
  // joining those runs would draw a line straight through it
  const runs = (s.inner_profiles ?? []) as [number, number][][];
  const inner: number[] = [];
  for (const run of runs) inner.push(...mirrored(run));
  return {
    outer: mirrored(profile),
    inner: new Float32Array(inner),
    axis: new Float32Array([...at(zLo, 0, 1), ...at(zHi, 0, 1)]),
  };
}

export const turningRolesMode: ViewMode = {
  id: 'turning',
  label: 'Turning roles',
  async paint(ctx) {
    const result = require(ctx);
    const roles = await turningField(ctx, result, 'turn_role') as Uint8Array;
    const regions = await turningField(ctx, result,
                                       'milled_region') as Uint32Array;

    // per-EFFECTIVE-face verdict: 254 marks a face that is part turned and
    // part not, which no single role describes and a user cut resolves
    const brepDesc = effectiveDescriptor(ctx.manifest);
    const defaultsDesc = ctx.manifest.fields.find(
      (f) => f.id === `results.cnc.turning.${result.hash}.brep_default`);
    const [brepIds, defaults] = await Promise.all([
      brepDesc ? ctx.getField(brepDesc) as Promise<Uint32Array> : null,
      defaultsDesc ? ctx.getField(defaultsDesc) as Promise<Uint8Array> : null,
    ]);
    const mixed = (f: number) => {
      if (!brepIds || !defaults) return false;
      const b = brepIds[f];
      // ids past the array are sub-faces the result predates — treat them as
      // needing attention until the auto re-run lands, as setups does
      return b >= defaults.length || defaults[b] === CONFLICT_ROLE;
    };

    const tracker = new FocusTracker(ctx);
    const counts = new Array(ROLE_LABELS.length).fill(0);
    ctx.paintFaces((f) => {
      const code = roles[f];
      if (code < counts.length) counts[code]++;
      tracker.add(`role:${code}`, f);
      if (regions[f]) tracker.add(`region:${regions[f]}`, f);
      if (mixed(f)) {
        tracker.add('needs_split', f);
        // spatially truthful: the turned part keeps its role color, the part
        // that is not a surface of revolution takes the conflict color, so
        // the cut line the face wants is visible before it is made
        if (code === 0) return CONFLICT_COLOR;
      }
      return ROLE_COLORS[code] ?? COL.inaccess;
    });
    // the leftovers are exactly what still needs a mill
    ctx.setFindings((f) => roles[f] === 0);

    const legend: LegendEntry[] = ROLE_LABELS
      .map((label, code) => ({
        color: ROLE_COLORS[code] ?? COL.inaccess,
        label: `${label} (${counts[code]})`,
        focus: tracker.focus(`role:${code}`),
      }))
      .filter((_, code) => counts[code] > 0);

    for (const region of (result.stats.milled_regions ?? []) as any[]) {
      legend.push({
        color: COL.inaccess,
        label: `milled region ${region.id} — ${Number(region.area).toFixed(0)} mm²`,
        focus: tracker.focus(`region:${region.id}`),
      });
    }
    const { outer, inner, axis } = sectionLines(result);
    if (outer.length) ctx.setLines(outer, SECTION_COLOR, false);
    if (inner.length) ctx.setLines(inner, INNER_COLOR, false);
    ctx.setLines(axis, AXIS_COLOR, false);
    if (outer.length) {
      legend.push({ color: SECTION_COLOR, label: 'turned state — outer' });
    }
    if (inner.length) {
      legend.push({ color: INNER_COLOR, label: 'turned state — bores' });
    }

    const faceCount = Number(result.stats.needs_split ?? 0);
    if (faceCount) {
      const area = Number(result.stats.needs_split_area ?? 0);
      legend.push({
        color: CONFLICT_COLOR,
        label: `needs split — ${faceCount} face(s), ${area.toFixed(0)} mm²`,
        focus: tracker.focus('needs_split'),
      });
    }

    let stats = summary(result);
    if (brepIds) {
      const splitLines = await drawSplitOverlays(ctx, turningSplitHost, brepIds);
      if (splitLines.length) stats += `\n${splitLines.join('\n')}`;
    }
    if (faceCount) {
      stats += '\nmagenta = turned and milled on one face — split it so each '
        + 'piece classifies on its own';
    }
    return { legend, stats };
  },
};

/** Split-interaction wiring for the turning roles view. */
export const turningSplitHost: SplitHost = {
  processId: 'cnc',
  modeId: 'turning',
  currentResult: (manifest: Manifest) => {
    const results = turningResults(manifest);
    return results[results.length - 1];
  },
  analysisOf: () => 'turning',
  resultParam: 'turningResult',
};

/** Turning-mode section of the CNC controls: just the split interaction. */
export function TurningControls() {
  return <SplitControls host={turningSplitHost} />;
}

export const turningResidualMode: ViewMode = {
  id: 'turning_residual',
  label: 'Revolution error',
  async paint(ctx) {
    const result = require(ctx);
    // per-FACE field, so this cannot go through heatmapMode (that factory
    // takes the per-face mean of a per-VERTEX field, which would also blur the
    // sharp turned/milled boundary this view exists to show)
    const residual = await turningField(ctx, result,
                                        'turn_residual') as Float32Array;
    const limit = Math.max(Number(result.stats.tollerance) || 1, 0.01);
    const scale = 10 * limit;

    let flagged = 0;
    ctx.paintFaces((f) => {
      const value = residual[f];
      if (value > limit) flagged++;
      return rampColor(Math.min(1, value / scale));
    });
    ctx.setFindings((f) => residual[f] > limit);

    return {
      legend: [
        { color: rampColor(0), label: `on the revolution (0°)` },
        { color: rampColor(limit / scale), label: `tolerance (${limit.toFixed(1)}°)` },
        { color: rampColor(1), label: `${scale.toFixed(0)}° or more off` },
      ],
      colorbar: {
        min: 0,
        max: scale,
        unit: 'deg',
        gradient: sequentialGradientCss(),
        threshold: limit,
      },
      stats: `${flagged} faces over ${limit.toFixed(1)}° · ${summary(result)}`,
    };
  },
};

// index == backend category code (turning.AXIS_ROLES)
const AXIS_ROLE_LABELS = ['off-axis', 'revolution-compatible', 'swept'];
const AXIS_ROLE_COLORS: RGB[] = [
  COL.inaccess,          // off-axis — the milled remainder
  [0.55, 0.75, 0.93],    // compatible but not swept (faces perpendicular)
  [0.30, 0.55, 0.85],    // actually swept by the lathe
];

/**
 * One candidate axis from `cnc/turning_scan`, painted per face.
 *
 * A separate mode from `turning_residual` rather than a parameterization of
 * it: that one is hardcoded to the `cnc/turning` result and its stats line
 * reads keys (verdict, stock, profile) the scan does not have. Pinned by
 * `scanHash` + `scanAxis` because per-cell runs leave many small results and
 * "the latest" is not the one the clicked cell reported.
 */
export const axisRoleMode: ViewMode = {
  id: 'axis_role',
  label: 'Turnability about one axis',
  async paint(ctx) {
    const hash = ctx.params.scanHash;
    const which = Number(ctx.params.scanAxis) || 0;
    const result = ctx.manifest.results.find(
      (r) => r.process === 'cnc' && r.analysis === 'turning_scan'
        && r.hash === hash);
    if (!result) {
      throw new Error('no turnability scan for this axis — compute it from '
        + 'the directions study');
    }
    const axis = ((result.stats as any).axes ?? [])[which];
    if (!axis) throw new Error(`the scan has no axis ${which}`);
    const desc = ctx.manifest.fields.find(
      (f) => f.id === `results.cnc.turning_scan.${result.hash}.${axis.field}`);
    if (!desc) throw new Error(`scan field "${axis.field}" missing — re-run it`);
    const roles = await ctx.getField(desc) as Uint8Array;

    const counts = [0, 0, 0];
    ctx.paintFaces((f) => {
      counts[roles[f]] = (counts[roles[f]] ?? 0) + 1;
      return AXIS_ROLE_COLORS[roles[f]] ?? COL.inaccess;
    });
    ctx.setFindings((f) => roles[f] === 0);

    const vector = (axis.vector ?? []).map((c: number) => c.toFixed(2)).join(', ');
    return {
      legend: AXIS_ROLE_LABELS.map((label, i) => ({
        color: AXIS_ROLE_COLORS[i], label: `${label} (${counts[i]})`,
      })),
      stats: `axis [${vector}] · `
        + `${(100 * axis.inlier_fraction).toFixed(1)}% revolvable, `
        + `${(100 * axis.radial_fraction).toFixed(1)}% swept`
        + (axis.qualified ? '' : ' — below the swept-area gate'),
    };
  },
};

/**
 * The UNION of several per-face masks — what a set of directions covers
 * together, which is the one thing per-pair stats cannot answer (they
 * overlap). Driven entirely by viewer params so the directions study can point
 * it at any column's fields without a mode per column:
 * `coverageFields` (manifest field ids), `coverageRule` (how that field
 * encodes "covered") and `coverageLabel` (what to call it in the legend).
 */
export const coverageMode: ViewMode = {
  id: 'coverage',
  label: 'Combined coverage',
  async paint(ctx) {
    const ids: string[] = ctx.params.coverageFields ?? [];
    const rule: CoverageRule = ctx.params.coverageRule ?? 'nonzero';
    const label: string = ctx.params.coverageLabel ?? 'covered';
    if (!ids.length) {
      throw new Error('no combined coverage selected — pick rows in the '
        + 'directions study and click its total');
    }
    const union = new Uint8Array(ctx.faceCount);
    let missing = 0;
    for (const id of ids) {
      const desc = ctx.manifest.fields.find((f) => f.id === id);
      if (!desc) { missing++; continue; }
      const mask = await ctx.getField(desc) as Uint8Array;
      for (let f = 0; f < union.length; f++) {
        if (covered(rule, mask[f])) union[f] = 1;
      }
    }

    let n = 0;
    ctx.paintFaces((f) => {
      if (!union[f]) return COL.inaccess;
      n++;
      return COL.ok;
    });
    ctx.setFindings((f) => !union[f]);

    return {
      legend: [
        { color: COL.ok, label: `${label} (${n})` },
        { color: COL.inaccess, label: `not ${label} (${ctx.faceCount - n})` },
      ],
      stats: `${ids.length - missing} of ${ids.length} contributing`
        + ` · ${n} of ${ctx.faceCount} faces ${label}`
        + (missing ? ` · ${missing} not computed` : ''),
    };
  },
};

/** Inspect lines for one clicked face (appended by the cnc plugin). */
export async function inspectTurning(face: number,
                                     ctx: ViewCtx): Promise<string[]> {
  const result = latestTurning(ctx);
  if (!result) return [];
  try {
    const roles = await turningField(ctx, result, 'turn_role') as Uint8Array;
    const residual = await turningField(ctx, result,
                                        'turn_residual') as Float32Array;
    const lines = [`turning role: ${ROLE_LABELS[roles[face]] ?? 'unknown'}`,
                   `revolution error: ${residual[face].toFixed(2)}°`];

    // (z, r) is derived client-side from the axis rather than shipped per face
    const p = result.stats.axis.point as Vec3;
    const d = result.stats.axis.direction as Vec3;
    let cx = 0;
    let cy = 0;
    let cz = 0;
    for (let k = 0; k < 3; k++) {
      const v = ctx.faces[3 * face + k];
      cx += ctx.verts[3 * v] / 3;
      cy += ctx.verts[3 * v + 1] / 3;
      cz += ctx.verts[3 * v + 2] / 3;
    }
    const rel: Vec3 = [cx - p[0], cy - p[1], cz - p[2]];
    const z = rel[0] * d[0] + rel[1] * d[1] + rel[2] * d[2];
    const radius = Math.hypot(rel[0] - z * d[0], rel[1] - z * d[1],
                              rel[2] - z * d[2]);
    lines.push(`axis position: z ${z.toFixed(2)} mm · r ${radius.toFixed(2)} mm`);
    return lines;
  } catch {
    return [];
  }
}
