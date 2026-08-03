// Machined-feature recognition view: paints the per-face feature category
// from the latest cnc/features result and lists every dimensioned feature
// as a click-to-fly legend entry.

import { COL, fetchFaceField, FocusTracker } from '../../colorizers/core';
import type { ResultEntry } from '../../api/types';
import type {
  LegendEntry, RGB, ViewCtx, ViewMode,
} from '../../registry/types';

// keep in sync with FEATURES_SCHEMA in processes/cnc.py
export const FEATURES_SCHEMA = 2;

// index == backend category code (machining_features.FEATURE_TYPES)
const TYPE_CODES = [
  'none', 'through_hole', 'blind_hole', 'counterbore', 'countersink', 'pocket',
];
const TYPE_COLORS: RGB[] = [
  COL.ok,                 // none
  [0.44, 0.64, 0.86],     // through hole
  [0.3, 0.45, 0.72],      // blind hole
  [0.95, 0.66, 0.23],     // counterbore
  [0.88, 0.29, 0.23],     // countersink
  [0.62, 0.8, 0.58],      // pocket
];

export function latestFeatures(ctx: ViewCtx): ResultEntry | null {
  const results = ctx.manifest.results.filter((r) => r.process === 'cnc'
    && r.analysis === 'features' && !r.stale
    && r.params.schema === FEATURES_SCHEMA);
  return results.length ? results[results.length - 1] : null;
}

export function featureLabel(feature: any): string {
  const size = feature.diameter != null
    ? `Ø${feature.diameter.toFixed(2)}` : 'freeform';
  const extra = feature.counterbore_diameter != null
    ? ` / Ø${feature.counterbore_diameter.toFixed(2)}`
    : feature.angle != null ? ` ${feature.angle.toFixed(0)}°` : '';
  return `${size}${extra} ${feature.type.replace('_', ' ')} · depth ${feature.depth.toFixed(1)}`;
}

/** Feature arrays, broadcast from BREP faces onto whatever mesh is loaded.
 *
 * Recognition is a BREP question, so the result is stored per BREP face —
 * which is what lets this lens work on the coarse preview, before the fine
 * remesh exists. `loadBrepFaceIds` hands back the fine map when there is one
 * and the coarse map otherwise, so the join is the same either way. */
async function featureFields(ctx: ViewCtx, result: ResultEntry) {
  const find = (name: string) => ctx.manifest.fields.find(
    (f) => f.id === `results.cnc.features.${result.hash}.${name}`);
  const categoryDesc = find('feature_category');
  const idDesc = find('feature_id');
  if (!categoryDesc || !idDesc) throw new Error('feature fields missing — re-run cnc/features');
  const [category, ids] = await Promise.all([
    fetchFaceField(ctx, categoryDesc) as Promise<Uint8Array>,
    fetchFaceField(ctx, idDesc) as Promise<Uint32Array>,
  ]);
  return { category, ids };
}

export const featuresMode: ViewMode = {
  id: 'features',
  // no user knobs: what this paint reads is bound by the study/check
  params: [],
  label: 'Machined features',
  async paint(ctx) {
    const result = latestFeatures(ctx);
    if (!result) {
      throw new Error('no feature recognition result — run cnc/features in the Compute rail (needs prep/aag)');
    }
    const { category, ids } = await featureFields(ctx, result);
    const tracker = new FocusTracker(ctx);
    ctx.paintFaces((f) => {
      if (ids[f]) tracker.add(`feature:${ids[f]}`, f);
      return TYPE_COLORS[category[f]] ?? COL.inaccess;
    });

    const features: any[] = result.stats.features ?? [];
    const legend: LegendEntry[] = features.slice(0, 24).map((feature) => ({
      color: TYPE_COLORS[TYPE_CODES.indexOf(feature.type)] ?? COL.side,
      label: featureLabel(feature),
      focus: tracker.focus(`feature:${feature.id}`),
    }));
    legend.push({ color: COL.ok, label: 'no feature' });

    const counts = result.stats.counts ?? {};
    const summary = Object.entries(counts)
      .map(([type, n]) => `${n} ${type.replace('_', ' ')}`).join(', ');
    return { legend, stats: summary || 'no features recognized' };
  },
};

/** Inspect lines for one clicked face (appended by the cnc plugin). */
export async function inspectFeature(face: number, ctx: ViewCtx): Promise<string[]> {
  const result = latestFeatures(ctx);
  if (!result) return [];
  try {
    const { ids } = await featureFields(ctx, result);
    const id = ids[face];
    if (!id) return [];
    const feature = (result.stats.features ?? []).find((f: any) => f.id === id);
    return feature ? [`feature: ${featureLabel(feature)}`] : [];
  } catch {
    return [];
  }
}
