// The lens behind an expression check: paints exactly the mask the check
// counts, from exactly the same builder.
//
// The number and the picture come from one call to `buildMask`, so they
// cannot disagree — the same discipline `coverage.ts` enforces between the
// study's union total and the union it paints.
//
// Two colours, not one: the SELECTED faces in the finding colour, and — while
// an expression is being built — the faces the first term alone would have
// picked, faded. Seeing what a later term removed is most of what makes a
// multi-term rule readable.

import { COL, FocusTracker } from './core';
import { buildMask, summarize, termMask, type ResolvedTerm } from '../fields/expression';
import type { PaintInfo, ViewCtx, ViewMode } from '../registry/types';

const pct = (v: number) => `${(100 * v).toFixed(1)} %`;

export const expressionMode: ViewMode = {
  id: 'expression',
  label: 'Check expression',
  async paint(ctx: ViewCtx): Promise<PaintInfo> {
    const terms = (ctx.params.exprTerms ?? []) as ResolvedTerm[];
    if (!terms.length) {
      throw new Error('no terms yet — add a field to the expression');
    }
    const { mask, unresolved } = await buildMask(ctx, terms);
    // the first term alone, as context for what the rest carved away
    const scope = terms.length > 1 ? await termMask(ctx, terms[0]) : null;

    const tracker = new FocusTracker(ctx);
    ctx.paintFaces((f) => {
      if (mask[f]) { tracker.add('hit', f); return COL.band; }
      if (scope?.[f]) return COL.below;
      return null;
    });
    ctx.setFindings((f) => !!mask[f]);

    const summary = summarize(ctx, mask);
    const legend = [
      { color: COL.band, label: 'selected by the expression', focus: tracker.focus('hit') },
      ...(scope ? [{ color: COL.below, label: 'first term only (context)' }] : []),
    ];
    const note = unresolved.length
      ? ` · ${unresolved.length} term(s) not computed` : '';
    return {
      legend,
      stats: `${summary.faces} faces · ${summary.area.toFixed(0)} mm² · `
        + `${pct(summary.share)} of the part${note}`,
    };
  },
};
