// Hull roughing view: paints the residual pockets from the latest cnc/roughing
// result — what is still left to cut once the stock has been roughed down to
// the part's convex hull. Faces on the hull stay unpainted (nothing to remove
// there); every off-hull face carries the id of the pocket it bounds, and the
// legend names each pocket by volume and by the largest tool that reaches all
// of it. Pockets no tool in the library fully reaches are the findings.

import { COL, fetchFaceField, FocusTracker } from '../../colorizers/core';
import type { ResultEntry } from '../../api/types';
import type {
  LegendEntry, RGB, ViewCtx, ViewMode,
} from '../../registry/types';

// keep in sync with ROUGHING_SCHEMA in processes/cnc.py
export const ROUGHING_SCHEMA = 1;

// cycled per pocket; a pocket no tool reaches overrides this with COL.tip
const POCKET_COLORS: RGB[] = [
  [0.44, 0.64, 0.86],
  [0.62, 0.80, 0.58],
  [0.95, 0.66, 0.23],
  [0.72, 0.42, 0.55],
  [0.38, 0.68, 0.66],
  [0.85, 0.72, 0.32],
  [0.55, 0.55, 0.82],
];

export function latestRoughing(ctx: ViewCtx): ResultEntry | null {
  const results = ctx.manifest.results.filter((r) => r.process === 'cnc'
    && r.analysis === 'roughing' && !r.stale
    && r.params.schema === ROUGHING_SCHEMA);
  return results.length ? results[results.length - 1] : null;
}

function pocketColor(pocket: any, index: number): RGB {
  // fully_reachable is absent when the run carried no tool library at all —
  // only an explicit false is a finding, not "we never checked"
  if (pocket.fully_reachable === false) return COL.tip;
  return POCKET_COLORS[index % POCKET_COLORS.length];
}

export function pocketLabel(pocket: any): string {
  const volume = pocket.volume >= 1000
    ? `${(pocket.volume / 1000).toFixed(2)} cm³`
    : `${pocket.volume.toFixed(1)} mm³`;
  const tool = pocket.fully_reachable === false ? 'no tool reaches it'
    : pocket.best_tool_diameter != null ? `Ø${pocket.best_tool_diameter}`
      : 'reach not checked';
  return `pocket ${pocket.id} · ${volume} · ${pocket.max_depth.toFixed(1)} mm deep · ${tool}`;
}

async function pocketField(ctx: ViewCtx, result: ResultEntry) {
  const desc = ctx.manifest.fields.find(
    (f) => f.id === `results.cnc.roughing.${result.hash}.pocket_id`);
  if (!desc) throw new Error('pocket field missing — re-run cnc/roughing');
  return await fetchFaceField(ctx, desc) as Uint32Array;
}

export const roughingMode: ViewMode = {
  id: 'roughing',
  label: 'Hull roughing pockets',
  async paint(ctx) {
    const result = latestRoughing(ctx);
    if (!result) {
      throw new Error('no roughing result — run cnc/roughing in the Compute panel');
    }
    const pocketId = await pocketField(ctx, result);
    const pockets: any[] = result.stats.pockets ?? [];
    const byId = new Map<number, { pocket: any, color: RGB }>();
    pockets.forEach((pocket, index) => {
      byId.set(pocket.id, { pocket, color: pocketColor(pocket, index) });
    });

    const tracker = new FocusTracker(ctx);
    ctx.paintFaces((f) => {
      const entry = byId.get(pocketId[f]);
      if (!entry) return null; // on the hull already — nothing to rough here
      tracker.add(`pocket:${pocketId[f]}`, f);
      return entry.color;
    });
    // the DFM finding is a pocket the library cannot clear, not every pocket
    ctx.setFindings((f) => byId.get(pocketId[f])?.pocket.fully_reachable === false);

    const legend: LegendEntry[] = pockets.slice(0, 24).map((pocket, index) => ({
      color: pocketColor(pocket, index),
      label: pocketLabel(pocket),
      focus: tracker.focus(`pocket:${pocket.id}`),
    }));

    const s = result.stats;
    const parts = [
      `${s.pocket_count} pocket${s.pocket_count === 1 ? '' : 's'}`,
      `${s.labeled_volume.toFixed(0)} mm³ left after the hull`,
      `of ${s.residual_volume.toFixed(0)} mm³ exact (${(100 * s.volume_error).toFixed(1)}% off at ${s.voxel.toFixed(2)} mm voxels)`,
    ];
    if (s.unreachable_volume) {
      parts.push(`${s.unreachable_volume.toFixed(0)} mm³ no tool reaches`);
    }
    return { legend, stats: parts.join(' · ') };
  },
};

/** Inspect lines for one clicked face (appended by the cnc plugin). */
export async function inspectRoughing(face: number, ctx: ViewCtx): Promise<string[]> {
  const result = latestRoughing(ctx);
  if (!result) return [];
  try {
    const pocketId = await pocketField(ctx, result);
    if (!pocketId[face]) return [];
    const pocket = (result.stats.pockets ?? []).find(
      (p: any) => p.id === pocketId[face]);
    return pocket ? [`roughing: ${pocketLabel(pocket)}`] : [];
  } catch {
    return [];
  }
}
