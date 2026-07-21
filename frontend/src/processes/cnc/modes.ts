// The seven CNC view modes, ported one-to-one from the legacy viewer.html
// update() dispatcher.

import {
  COL, faceAngles, faceBlocked, faceValues, FocusTracker, heatmapMode, percentile, rampColor,
} from '../../colorizers/core';
import type { PaintInfo, ViewCtx, ViewMode } from '../../registry/types';
import {
  accessKeep, faceAccess, requireSource, vertexGap, vertexMinStickout,
} from './compose';
import { currentTip, holderCylinders, wallThreshold } from './sources';

const num = (value: any) => {
  const parsed = parseFloat(value);
  return isFinite(parsed) ? parsed : NaN;
};

// the thin_span field is computed by the (process-agnostic) injection
// molding analysis; surface it here too — weak flexible features matter
// for machining as much as for molding
function spanField(ctx: ViewCtx) {
  const results = ctx.manifest.results.filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'thin_span');
  const result = results[results.length - 1];
  const fieldId = result?.fields.find((f) => f.endsWith('.span_ratio'));
  return fieldId ? ctx.manifest.fields.find((f) => f.id === fieldId) ?? null : null;
}

export const thinSpanMode = heatmapMode(
  'thinSpan', 'Thin span / stiffness heatmap',
  spanField,
  {
    flagDirection: 'above',
    thresholdParam: 'maxSpanRatio',
    scaleParam: 'spanScale',
    units: '×',
    okLabel: 'well supported — ok',
  });

export const unifiedMode: ViewMode = {
  id: 'unified',
  label: 'Unified verdict (tool + holder)',
  async paint(ctx): Promise<PaintInfo> {
    const source = requireSource(ctx);
    const tip = currentTip(source, ctx.params);
    if (!tip) throw new Error('no tip fields cached — run precompute with tips');
    const tol = num(ctx.params.tolerance) || 0;
    const rule = ctx.params.rule;
    const stickoutVal = num(ctx.params.stickout);
    const gap = await vertexGap(ctx, source, tip);
    const access = await faceAccess(ctx, source);
    const minStick = await vertexMinStickout(ctx, source, tip);
    const useHolder = !!minStick && isFinite(stickoutVal);
    const angles = faceAngles(ctx, ctx.directions[source.direction]);
    const wallTol = num(ctx.params.wallTol) || 0;
    const sideMill = !!ctx.params.sideMill;
    const wallGapTol = wallThreshold(source, tol);
    let nTip = 0, nWall = 0, nHold = 0, nOk = 0, nInv = 0, nSide = 0;
    const tracker = new FocusTracker(ctx); // legend click -> fly to the group
    ctx.paintFaces((f) => {
      if (access && !access[f]) { nInv++; tracker.add('inaccess', f); return COL.inaccess; }
      const holderBlocked = useHolder
        && faceBlocked(ctx, f, (v) => minStick!.values[v] > stickoutVal + tol, rule);
      if (sideMill && Math.abs(angles[f] - 90) <= wallTol) {
        // near-vertical wall: finished by the tool flank. The gap field
        // still decides whether the flank can get there at all (a slot
        // narrower than the tool fills up in the closing), just with a
        // pixel-noise-proof threshold.
        if (faceBlocked(ctx, f, (v) => gap[v] > wallGapTol, rule)) { nWall++; tracker.add('tip', f); return COL.tip; }
        if (holderBlocked) { nHold++; tracker.add('holder', f); return COL.holder; }
        nSide++; tracker.add('side', f); return COL.side;
      }
      if (faceBlocked(ctx, f, (v) => gap[v] > tol, rule)) { nTip++; tracker.add('tip', f); return COL.tip; }
      if (holderBlocked) { nHold++; tracker.add('holder', f); return COL.holder; }
      nOk++; tracker.add('ok', f); return COL.ok;
    });
    // findings = everything not producible in this setup (blocked walls/tips,
    // holder collisions, undercuts) — the faces an engineer must act on
    const problem = new Set<number>();
    for (const key of ['tip', 'holder', 'inaccess']) {
      for (const f of tracker.focus(key)?.faces ?? []) problem.add(f);
    }
    ctx.setFindings((f) => problem.has(f));
    const legend = [
      { color: COL.ok, label: 'reachable (tool bottom)', focus: tracker.focus('ok') },
      { color: COL.tip, label: 'blocked — tool cannot reach', focus: tracker.focus('tip') },
      { color: COL.inaccess, label: 'not accessible (undercut)', focus: tracker.focus('inaccess') },
    ];
    if (sideMill) legend.splice(1, 0, { color: COL.side, label: 'wall — side-milled', focus: tracker.focus('side') });
    if (useHolder) {
      legend.splice(legend.length - 1, 0,
        { color: COL.holder, label: 'blocked by holder / stickout', focus: tracker.focus('holder') });
    }
    const stats = `reachable ${nOk}` + (sideMill ? ` · side-milled ${nSide}` : '')
      + ` · blocked ${nTip + nWall}` + (sideMill && nWall ? ` (${nWall} walls)` : '')
      + (useHolder ? ` · holder ${nHold}` : '') + ` · inaccessible ${nInv}`
      + (useHolder && minStick!.approx
        ? '\n⚠ holder check is vertex-centred (no sreq fields for this tip)' : '')
      + (!useHolder && holderCylinders(ctx.params.holder).length
        ? '\n(enter a stickout to apply the holder)' : '');
    return { legend, stats };
  },
};

export const accessMode: ViewMode = {
  id: 'access',
  label: 'Accessibility (undercuts)',
  async paint(ctx): Promise<PaintInfo> {
    const source = requireSource(ctx);
    const access = await faceAccess(ctx, source);
    if (!access) throw new Error('no accessibility.npy for this direction');
    let n = 0;
    const tracker = new FocusTracker(ctx); // legend click -> fly to the group
    ctx.paintFaces((f) => {
      if (access[f]) { n++; tracker.add('ok', f); return COL.ok; }
      tracker.add('inaccess', f); return COL.inaccess;
    });
    ctx.setFindings((f) => !access[f]);
    return {
      legend: [
        { color: COL.ok, label: 'accessible (not an undercut)', focus: tracker.focus('ok') },
        { color: COL.inaccess, label: 'undercut for this direction', focus: tracker.focus('inaccess') },
      ],
      stats: `${n} of ${ctx.faceCount} faces accessible`,
    };
  },
};

export const classMode: ViewMode = {
  id: 'class',
  label: 'Surface class (normal vs direction)',
  async paint(ctx): Promise<PaintInfo> {
    const source = requireSource(ctx);
    const angles = faceAngles(ctx, ctx.directions[source.direction]);
    const wallTol = num(ctx.params.wallTol) || 0;
    const keep = await accessKeep(ctx, source);
    let nBottom = 0, nChamfer = 0, nWall = 0, nSlope = 0, nOver = 0;
    const tracker = new FocusTracker(ctx); // legend click -> fly to the group
    ctx.paintFaces((f) => {
      if (keep && !keep(f)) { tracker.add('inaccess', f); return COL.inaccess; }
      const a = angles[f];
      if (a <= wallTol) { nBottom++; tracker.add('bottom', f); return COL.floor; }
      if (Math.abs(a - 45) <= wallTol) { nChamfer++; tracker.add('chamfer', f); return COL.chamfer; }
      if (Math.abs(a - 90) <= wallTol) { nWall++; tracker.add('wall', f); return COL.side; }
      if (a > 90) { nOver++; tracker.add('over', f); return COL.overhang; }
      nSlope++; tracker.add('slope', f); return COL.slope;
    });
    return {
      legend: [
        { color: COL.floor, label: `≈ 0° (±${wallTol}°) — bottom milling`, focus: tracker.focus('bottom') },
        { color: COL.chamfer, label: `≈ 45° (±${wallTol}°) — chamfer milling`, focus: tracker.focus('chamfer') },
        { color: COL.side, label: `≈ 90° (±${wallTol}°) — wall milling`, focus: tracker.focus('wall') },
        { color: COL.slope, label: 'slope (ball / step milling)', focus: tracker.focus('slope') },
        { color: COL.overhang, label: '> 90° — overhang for this direction', focus: tracker.focus('over') },
        { color: COL.inaccess, label: 'inaccessible (greyed)', focus: tracker.focus('inaccess') },
      ],
      stats: `bottom ${nBottom} · chamfer ${nChamfer} · wall ${nWall} · slope ${nSlope} · overhang ${nOver}`,
    };
  },
};

export const gapMode: ViewMode = {
  id: 'gap',
  label: 'Tip gap heatmap',
  async paint(ctx): Promise<PaintInfo> {
    const source = requireSource(ctx);
    const tip = currentTip(source, ctx.params);
    if (!tip) throw new Error('no tip fields cached — run precompute with tips');
    const tol = num(ctx.params.tolerance) || 0;
    const rule = ctx.params.rule;
    const gap = await vertexGap(ctx, source, tip);
    const keep = await accessKeep(ctx, source);
    const vals = faceValues(ctx, gap, keep);
    const angles = faceAngles(ctx, ctx.directions[source.direction]);
    const wallTol = num(ctx.params.wallTol) || 0;
    const wallGapTol = wallThreshold(source, tol);
    const thr = (f: number) => (Math.abs(angles[f] - 90) <= wallTol ? wallGapTol : tol);
    const auto = Math.max(percentile(vals, 0.98), tol * 3, 0.2);
    const max = num(ctx.params.scale) || auto;
    ctx.paintFaces((f) => {
      if (isNaN(vals[f])) return COL.inaccess;
      const t = thr(f);
      return vals[f] <= t ? COL.below : rampColor((vals[f] - t) / (max - t));
    });
    let n = 0;
    for (let f = 0; f < ctx.faceCount; f++) {
      if (!isNaN(vals[f]) && faceBlocked(ctx, f, (v) => gap[v] > thr(f), rule)) n++;
    }
    return {
      legend: [
        { color: COL.below, label: 'gap ≤ threshold (tool reaches)' },
        { color: rampColor(0.001), label: 'just above threshold' },
        { color: rampColor(1), label: `gap ≥ ${max.toFixed(2)} mm` },
        { color: COL.inaccess, label: 'inaccessible (greyed)' },
      ],
      stats: `${n} accessible faces blocked at tolerance ${tol}`
        + ` (walls thresholded at ${wallGapTol.toFixed(2)} to absorb pixel noise)`
        + ` · auto max ${auto.toFixed(2)}`,
    };
  },
};

export const stickoutMode: ViewMode = {
  id: 'stickout',
  label: 'Required stickout heatmap',
  async paint(ctx): Promise<PaintInfo> {
    const source = requireSource(ctx);
    const minStick = await vertexMinStickout(ctx, source, currentTip(source, ctx.params));
    if (!minStick) throw new Error('enter a holder stack to compute required stickout');
    const tol = num(ctx.params.tolerance) || 0;
    const keep = await accessKeep(ctx, source);
    const vals = faceValues(ctx, minStick.values, keep);
    const auto = Math.max(percentile(vals, 0.98), 1);
    const max = num(ctx.params.scale) || auto;
    ctx.paintFaces((f) => {
      if (isNaN(vals[f])) return COL.inaccess;
      return vals[f] <= tol ? COL.below : rampColor(vals[f] / max);
    });
    return {
      legend: [
        { color: COL.below, label: 'no holder constraint' },
        { color: rampColor(0.5), label: `${(max / 2).toFixed(1)} mm needed` },
        { color: rampColor(1), label: `≥ ${max.toFixed(1)} mm needed` },
        { color: COL.inaccess, label: 'inaccessible (greyed)' },
      ],
      stats: minStick.approx
        ? '⚠ vertex-centred approximation (no sreq fields for this tip — re-run precompute)'
        : 'tip-aware stickout (holder at feasible axis positions)',
    };
  },
};
