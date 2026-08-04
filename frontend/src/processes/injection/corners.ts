// The mold-cavity mirror of the CNC sharp-corner lens.
//
// A part's CONVEX edges are the internal corners of the cavity that forms it,
// so "which corners can the mold be milled sharp from the pull direction" is
// the same question cnc/corner_access already answers — asked with
// edge_class="convex" and pointed at the mold option's pull direction. Same
// analysis, same painter; only the class and the direction differ.

import { cornersMode } from '../cnc/corners';
import type { Manifest, ResultEntry } from '../../api/types';
import type { ViewCtx } from '../../registry/types';

// keep in sync with MOLD_SCHEMA in processes/injection_molding.py
const MOLD_SCHEMA = 4;

/** The A-side pull direction of the mold option the viewer is showing.
 * `stats.options[k].pair` is [i, i+1] into directions.npy, so it drops
 * straight into a corner result's direction list. */
function moldPullDirection(ctx: ViewCtx): number | null {
  const results = (ctx.manifest as Manifest).results.filter(
    (r: ResultEntry) => r.process === 'injection_molding'
      && r.analysis === 'mold_orientation' && r.stats.schema === MOLD_SCHEMA);
  const index = ctx.params.result;
  const result = (index != null && index >= 0 && index < results.length)
    ? results[index] : results[results.length - 1];
  const options = result?.stats.options ?? [];
  const option = options[ctx.params.option ?? 0] ?? options[0];
  const pair = option?.pair;
  return Array.isArray(pair) && pair.length ? Number(pair[0]) : null;
}

export const moldCornersMode = cornersMode({
  id: 'moldCorners',
  label: 'Mold sharp corners',
  edgeClass: 'convex',
  pickDirection: moldPullDirection,
});
