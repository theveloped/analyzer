// Turning views over the cnc/turning result: per-face turning roles with the
// lathe section drawn through the part, and the revolution-error heatmap that
// answers "why isn't this face turned".

import type { ResultEntry } from '../../api/types';
import { COL, FocusTracker, rampColor } from '../../colorizers/core';
import { sequentialGradientCss } from '../../viewer/colormaps';
import type {
  LegendEntry, RGB, ViewCtx, ViewMode,
} from '../../registry/types';

// keep in sync with TURNING_SCHEMA in processes/cnc.py
export const TURNING_SCHEMA = 1;

// index == backend category code (turning.TURN_ROLES)
const ROLE_LABELS = ['milled / other', 'OD turning', 'facing', 'boring',
                     'internal face', 'on axis'];
// external operations blue-ish, internal ones red/orange-ish — the convention
// the Analysis Situs turning recognizer uses, so the split reads at a glance
const ROLE_COLORS: RGB[] = [
  COL.inaccess,          // milled / other
  [0.30, 0.55, 0.85],    // OD turning
  [0.55, 0.75, 0.93],    // facing
  [0.88, 0.35, 0.28],    // boring
  [0.95, 0.63, 0.35],    // internal face
  [0.60, 0.60, 0.66],    // on axis
];
const SECTION_COLOR: RGB = [1.0, 0.85, 0.25];
const AXIS_COLOR: RGB = [0.55, 0.85, 0.55];

type Vec3 = [number, number, number];

export function latestTurning(ctx: ViewCtx): ResultEntry | null {
  const results = ctx.manifest.results.filter((r) => r.process === 'cnc'
    && r.analysis === 'turning' && !r.stale
    && r.params.schema === TURNING_SCHEMA);
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
 * -u) are drawn so the result reads as a lathe section through the part, plus
 * the centreline.
 */
function sectionLines(result: ResultEntry): {
  section: Float32Array; axis: Float32Array;
} {
  const s = result.stats;
  const p = s.axis.point as Vec3;
  const d = s.axis.direction as Vec3;
  const u = perpendicular(d);
  const profile = (s.profile ?? []) as [number, number][];

  const at = (z: number, r: number, sign: number): Vec3 => [
    p[0] + z * d[0] + sign * r * u[0],
    p[1] + z * d[1] + sign * r * u[1],
    p[2] + z * d[2] + sign * r * u[2],
  ];

  const segments: number[] = [];
  for (const sign of [1, -1]) {
    for (let i = 0; i + 1 < profile.length; i++) {
      const a = at(profile[i][0], profile[i][1], sign);
      const b = at(profile[i + 1][0], profile[i + 1][1], sign);
      segments.push(...a, ...b);
    }
  }

  const zLo = profile.length ? profile[0][0] : 0;
  const zHi = profile.length ? profile[profile.length - 1][0] : 0;
  const axis = new Float32Array([...at(zLo, 0, 1), ...at(zHi, 0, 1)]);
  return { section: new Float32Array(segments), axis };
}

export const turningRolesMode: ViewMode = {
  id: 'turning',
  label: 'Turning roles',
  async paint(ctx) {
    const result = require(ctx);
    const roles = await turningField(ctx, result, 'turn_role') as Uint8Array;
    const regions = await turningField(ctx, result,
                                       'milled_region') as Uint32Array;

    const tracker = new FocusTracker(ctx);
    const counts = new Array(ROLE_LABELS.length).fill(0);
    ctx.paintFaces((f) => {
      const code = roles[f];
      if (code < counts.length) counts[code]++;
      tracker.add(`role:${code}`, f);
      if (regions[f]) tracker.add(`region:${regions[f]}`, f);
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
    for (const bore of (result.stats.bores ?? []) as any[]) {
      legend.push({
        color: ROLE_COLORS[3],
        label: `bore Ø${Number(bore.diameter).toFixed(2)}`
          + `${bore.through ? ' (through)' : ''}`,
      });
    }

    const { section, axis } = sectionLines(result);
    if (section.length) ctx.setLines(section, SECTION_COLOR, false);
    ctx.setLines(axis, AXIS_COLOR, false);

    return { legend, stats: summary(result) };
  },
};

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
