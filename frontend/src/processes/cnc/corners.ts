// Sharp-corner access lenses: draw every candidate BREP edge of a stored
// cnc/corner_access result, coloured by what radius it will actually come out
// with from one approach direction, and tint the faces those corners bound.
//
// One factory, two lenses. The CNC lens asks the question of the PART
// (concave edges — pocket corners a round cutter cannot reproduce); the
// molding lens asks it of the mold CAVITY, whose internal corners are the
// part's convex edges. Same result shape, same painter, different edge class
// and a different way of picking the direction.

import { COL, FocusTracker, fetchFaceField } from '../../colorizers/core';
import type { FieldDescriptor, Manifest, ResultEntry } from '../../api/types';
import type { LegendEntry, RGB, ViewCtx, ViewMode } from '../../registry/types';
import { currentSource } from './sources';

// keep in sync with CORNER_SCHEMA in processes/cnc.py
export const CORNER_SCHEMA = 1;

/** Role codes as pipeline.EDGE_ACCESS_ROLES orders them. The LABELS come off
 * the field descriptor (`params.types`) rather than being duplicated here —
 * only the ordering is structural, because the backend aggregates per edge by
 * taking the max. */
export const ROLE_NA = 0;
export const ROLE_SHARP = 1;
export const ROLE_RADIUS = 2;
export const ROLE_OBLIQUE = 3;
export const ROLE_BLOCKED = 4;

const ROLE_COLORS: Record<number, RGB> = {
  [ROLE_SHARP]: COL.floor,
  [ROLE_RADIUS]: COL.tip,
  [ROLE_OBLIQUE]: COL.holder,
  [ROLE_BLOCKED]: COL.inaccess,
};

const ROLE_LEGEND: Record<number, string> = {
  [ROLE_SHARP]: 'machinable sharp',
  [ROLE_RADIUS]: 'needs a fillet (R = D/2)',
  [ROLE_OBLIQUE]: 'needs a fillet (R ≥ D/2)',
  [ROLE_BLOCKED]: 'not reachable from here',
};

const PAINTED_ROLES = [ROLE_BLOCKED, ROLE_OBLIQUE, ROLE_RADIUS, ROLE_SHARP];

/** The keys stats.per_direction[].counts uses — pipeline.EDGE_ACCESS_ROLES. */
const ROLE_KEYS: Record<number, string> = {
  [ROLE_SHARP]: 'sharp',
  [ROLE_RADIUS]: 'radius',
  [ROLE_OBLIQUE]: 'oblique',
  [ROLE_BLOCKED]: 'blocked',
};

export type EdgeClass = 'concave' | 'convex' | 'both';

export interface CornerResult {
  entry: ResultEntry;
  hash: string;
  directions: number[];
  diameter: number;
  cornerRadius: number;
}

/** The corner result to paint: `params.cornerHash` pins one, otherwise the
 * latest stored run whose edge class matches this lens. A convex-class result
 * answers a different question from a concave one, so the molding lens must
 * not silently paint the CNC lens's run. */
export function findCorners(
  ctx: { manifest: Manifest; params: Record<string, any> }, edgeClass: EdgeClass,
): CornerResult {
  const all = ctx.manifest.results.filter(
    (r) => r.process === 'cnc' && r.analysis === 'corner_access'
      && !r.stale && r.params.schema === CORNER_SCHEMA);
  const wanted = ctx.params.cornerHash;
  const matching = all.filter(
    (r) => r.stats.edge_class === edgeClass || r.stats.edge_class === 'both');
  const entry = wanted
    ? all.find((r) => r.hash === wanted)
    : matching[matching.length - 1];
  if (!entry) {
    throw new Error(
      `no ${edgeClass} sharp-corner result — run cnc/corner_access with `
      + `edge_class "${edgeClass}" in the Compute panel`);
  }
  return {
    entry,
    hash: entry.hash,
    directions: (entry.stats.directions ?? []) as number[],
    diameter: (entry.stats.tool?.diameter ?? 0) as number,
    cornerRadius: (entry.stats.tool?.corner_radius ?? 0) as number,
  };
}

function fieldOf(ctx: ViewCtx, result: CornerResult, name: string): FieldDescriptor {
  const id = `results.cnc.corner_access.${result.hash}.${name}`;
  const desc = ctx.manifest.fields.find((f) => f.id === id);
  if (!desc) throw new Error(`the corner result has no ${name} field — re-run it`);
  return desc;
}

export interface CornersModeOptions {
  id: string;
  label: string;
  edgeClass: EdgeClass;
  /** Fallback direction when the viewer has none pinned — the molding lens
   * points this at the selected mold option's pull direction. */
  pickDirection?(ctx: ViewCtx, result: CornerResult): number | null;
}

export function cornersMode(options: CornersModeOptions): ViewMode {
  return {
    id: options.id,
    label: options.label,
    async paint(ctx) {
      const result = findCorners(ctx, options.edgeClass);
      const preferred = ctx.params.cornerDirection;
      const picked = options.pickDirection?.(ctx, result) ?? null;
      const direction = [preferred, picked, result.directions[0]]
        .find((d) => typeof d === 'number' && result.directions.includes(d));
      if (direction === undefined) {
        throw new Error('the corner result covers no direction to show');
      }

      const segments = await ctx.getField(
        fieldOf(ctx, result, 'segment_points')) as Float32Array;
      const roles = await ctx.getField(
        fieldOf(ctx, result, `segment_role_${direction}`)) as Uint8Array;

      // one setLines call per role — the overlay is additive, and drawing
      // without depth test keeps a corner visible even when the part hides it
      const counts = new Map<number, number>();
      for (const role of PAINTED_ROLES) {
        const kept: number[] = [];
        for (let s = 0; s < roles.length; s++) {
          if (roles[s] !== role) continue;
          for (let k = 0; k < 6; k++) kept.push(segments[6 * s + k]);
        }
        counts.set(role, kept.length / 6);
        if (kept.length) ctx.setLines(new Float32Array(kept), ROLE_COLORS[role], false);
      }

      // the faces those corners bound, so the corner has somewhere to sit
      const flags = await fetchFaceField(
        ctx, fieldOf(ctx, result, `face_flag_${direction}`)) as Uint8Array;
      const tracker = new FocusTracker(ctx);
      ctx.paintFaces((f) => {
        if (!flags[f]) return null;
        tracker.add('flagged', f);
        return COL.slope;
      });
      ctx.setFindings((f) => !!flags[f]);

      const row = (result.entry.stats.per_direction ?? []).find(
        (r: any) => r.direction === direction);
      const roleCounts = row?.counts ?? {};
      const legend: LegendEntry[] = PAINTED_ROLES
        .filter((role) => (counts.get(role) ?? 0) > 0)
        .map((role) => ({
          color: ROLE_COLORS[role],
          label: `${ROLE_LEGEND[role]} · ${roleCounts[ROLE_KEYS[role]] ?? 0} edges`,
        }));
      if (tracker.count('flagged')) {
        legend.push({
          color: COL.slope,
          label: `faces bounding a flagged corner · ${tracker.count('flagged')}`,
          focus: tracker.focus('flagged'),
        });
      }

      const flagged = row?.flagged_edges ?? 0;
      const required = row?.max_required_radius ?? 0;
      // say so rather than quietly answering about a different approach
      const asked = typeof picked === 'number' ? picked : preferred;
      const substituted = typeof asked === 'number' && asked !== direction
        ? ` (asked for direction ${asked}; this run does not cover it — re-run to add it)`
        : '';
      return {
        legend,
        stats: `${flagged} of ${result.entry.stats.candidate_edges} sharp `
          + `${options.edgeClass} corners need R ≥ ${required.toFixed(2)} mm `
          + `from direction ${direction} · Ø${result.diameter} mm cutter`
          + (result.cornerRadius ? ` (rc ${result.cornerRadius})` : ' (flat)')
          + substituted,
      };
    },
  };
}

/** The part's own internal corners — what a mill cannot reproduce sharply.
 * Follows the CNC toolbar's direction selector when the stored run covers
 * that direction, so this lens and its neighbours answer about the same
 * approach rather than silently disagreeing. */
export const cncCornersMode = cornersMode({
  id: 'corners',
  label: 'Sharp corners',
  edgeClass: 'concave',
  pickDirection: (ctx) => currentSource(ctx.manifest, ctx.params)?.direction ?? null,
});
