// The seven CNC view modes, ported one-to-one from the legacy viewer.html
// update() dispatcher.

import {
  COL, faceAngles, faceBlocked, faceValues, percentile, rampColor,
} from '../../colorizers/core';
import type { PaintInfo, ViewMode } from '../../registry/types';
import {
  accessKeep, faceAccess, requireSource, vertexGap, vertexMinStickout,
} from './compose';
import { currentTip, holderCylinders, siblingSource, wallThreshold } from './sources';

const num = (value: any) => {
  const parsed = parseFloat(value);
  return isFinite(parsed) ? parsed : NaN;
};

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
    ctx.paintFaces((f) => {
      if (access && !access[f]) { nInv++; return COL.inaccess; }
      const holderBlocked = useHolder
        && faceBlocked(ctx, f, (v) => minStick!.values[v] > stickoutVal + tol, rule);
      if (sideMill && Math.abs(angles[f] - 90) <= wallTol) {
        // near-vertical wall: finished by the tool flank. The gap field
        // still decides whether the flank can get there at all (a slot
        // narrower than the tool fills up in the closing), just with a
        // pixel-noise-proof threshold.
        if (faceBlocked(ctx, f, (v) => gap[v] > wallGapTol, rule)) { nWall++; return COL.tip; }
        if (holderBlocked) { nHold++; return COL.holder; }
        nSide++; return COL.side;
      }
      if (faceBlocked(ctx, f, (v) => gap[v] > tol, rule)) { nTip++; return COL.tip; }
      if (holderBlocked) { nHold++; return COL.holder; }
      nOk++; return COL.ok;
    });
    const legend = [
      { color: COL.ok, label: 'reachable (tool bottom)' },
      { color: COL.tip, label: 'blocked — tool cannot reach' },
      { color: COL.inaccess, label: 'not accessible (undercut)' },
    ];
    if (sideMill) legend.splice(1, 0, { color: COL.side, label: 'wall — side-milled' });
    if (useHolder) {
      legend.splice(legend.length - 1, 0,
        { color: COL.holder, label: 'blocked by holder / stickout' });
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
    ctx.paintFaces((f) => (access[f] ? (n++, COL.ok) : COL.inaccess));
    return {
      legend: [
        { color: COL.ok, label: 'accessible (not an undercut)' },
        { color: COL.inaccess, label: 'undercut for this direction' },
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
    let nFloor = 0, nSlope = 0, nWall = 0, nOver = 0;
    ctx.paintFaces((f) => {
      if (keep && !keep(f)) return COL.inaccess;
      const a = angles[f];
      if (Math.abs(a - 90) <= wallTol) { nWall++; return COL.side; }
      if (a > 90) { nOver++; return COL.overhang; }
      if (a < 45) { nFloor++; return COL.floor; }
      nSlope++; return COL.slope;
    });
    return {
      legend: [
        { color: COL.floor, label: '< 45° — bottom milling' },
        { color: COL.slope, label: '45–90° — slope (ball / step milling)' },
        { color: COL.side, label: `wall (90° ± ${wallTol}°) — side milling` },
        { color: COL.overhang, label: '> 90° — overhang for this direction' },
        { color: COL.inaccess, label: 'inaccessible (greyed)' },
      ],
      stats: `floor ${nFloor} · slope ${nSlope} · wall ${nWall} · overhang ${nOver}`,
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

export const diffMode: ViewMode = {
  id: 'diff',
  label: 'Engine diff (zmap vs voxel)',
  async paint(ctx): Promise<PaintInfo> {
    const source = requireSource(ctx);
    const tip = currentTip(source, ctx.params);
    const other = siblingSource(ctx.manifest, source);
    const otherTip = other?.tips.find(
      (t) => tip && t.diameter === tip.diameter && t.corner_radius === tip.corner_radius);
    if (!tip || !other || !otherTip) {
      throw new Error(
        'diff needs the same tip cached for both engines (precompute engine zmap and voxel)');
    }
    const tol = num(ctx.params.tolerance) || 0;
    const rule = ctx.params.rule;
    const gapA = await vertexGap(ctx, source, tip);
    const gapB = await ctx.getField(otherTip.field) as Float32Array;
    const keep = await accessKeep(ctx, source);
    let nBoth = 0, nA = 0, nB = 0;
    ctx.paintFaces((f) => {
      if (keep && !keep(f)) return COL.inaccess;
      const a = faceBlocked(ctx, f, (v) => gapA[v] > tol, rule);
      const b = faceBlocked(ctx, f, (v) => gapB[v] > tol, rule);
      if (a && b) { nBoth++; return COL.both; }
      if (a) { nA++; return COL.zmapOnly; }
      if (b) { nB++; return COL.voxelOnly; }
      return COL.ok;
    });
    return {
      legend: [
        { color: COL.both, label: 'blocked in both engines' },
        { color: COL.zmapOnly, label: `${source.engine} only` },
        { color: COL.voxelOnly, label: `${other.engine} only` },
        { color: COL.ok, label: 'reachable in both' },
        { color: COL.inaccess, label: 'inaccessible (greyed)' },
      ],
      stats: `both ${nBoth} · ${source.engine} only ${nA} · ${other.engine} only ${nB}`,
    };
  },
};
