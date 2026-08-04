// Convex hull faces view: paints the per-face on-hull mask from the latest
// cnc/hull result (what an infinitely large mill machines directly from
// outside) and draws the hull's crease edges as a silhouette overlay.

import { COL, FocusTracker } from '../../colorizers/core';
import type { ResultEntry } from '../../api/types';
import type { ViewCtx, ViewMode } from '../../registry/types';

// keep in sync with HULL_SCHEMA in processes/cnc.py
export const HULL_SCHEMA = 1;

const EDGE_COLOR = [1.0, 0.85, 0.2] as const;

export function latestHull(ctx: ViewCtx): ResultEntry | null {
  const results = ctx.manifest.results.filter((r) => r.process === 'cnc'
    && r.analysis === 'hull' && !r.stale
    && r.params.schema === HULL_SCHEMA);
  return results.length ? results[results.length - 1] : null;
}

export const hullMode: ViewMode = {
  id: 'hull',
  // no user knobs: what this paint reads is bound by the study/check
  params: [],
  label: 'Convex hull faces',
  async paint(ctx) {
    const result = latestHull(ctx);
    if (!result) {
      throw new Error('no convex hull result — run cnc/hull in the Compute rail');
    }
    const find = (name: string) => ctx.manifest.fields.find(
      (f) => f.id === `results.cnc.hull.${result.hash}.${name}`);
    const maskDesc = find('on_hull');
    if (!maskDesc) throw new Error('hull mask missing — re-run cnc/hull');
    const mask = await ctx.getField(maskDesc) as Uint8Array;

    const tracker = new FocusTracker(ctx);
    let on = 0;
    ctx.paintFaces((f) => {
      if (mask[f]) {
        on += 1;
        tracker.add('on', f);
        return COL.floor;
      }
      tracker.add('off', f);
      return COL.tip;
    });
    ctx.setFindings((f) => !mask[f]);

    if (ctx.params.showHullEdges !== false) {
      const edgeDesc = find('hull_edges');
      if (edgeDesc) {
        const segments = await ctx.getField(edgeDesc) as Float32Array;
        if (segments.length) ctx.setLines(segments, EDGE_COLOR, false);
      }
    }

    const s = result.stats;
    return {
      legend: [
        { color: COL.floor, label: 'on the convex hull', focus: tracker.focus('on') },
        {
          color: COL.tip,
          label: 'inside the hull (needs a finite tool)',
          focus: tracker.focus('off'),
        },
      ],
      stats: `${on} of ${ctx.faceCount} faces on hull · `
        + `${(100 * (s.area_fraction ?? 0)).toFixed(1)}% of area · `
        + `eps ${(s.tollerance ?? 0).toFixed(3)} mm`,
    };
  },
};
