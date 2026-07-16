import { brepFacesMode, highlightsMode } from '../../colorizers/core';
import type { ProcessPlugin, ViewCtx } from '../../registry/types';
import { faceLabel, handleSplitPick } from '../../splits/splits';
import { useStore } from '../../state/store';
import { faceAccess, vertexGap, vertexMinStickout } from './compose';
import { CncControls } from './Controls';
import {
  accessMode, classMode, gapMode, stickoutMode, thinSpanMode, unifiedMode,
} from './modes';
import { cncSplitHost, loadSetups, setupsMode } from './setups';
import { currentSource, currentTip } from './sources';

async function inspect(face: number, ctx: ViewCtx): Promise<string[]> {
  const lines: string[] = [];

  try {
    const data = await loadSetups(ctx);
    const labels: string[] = data.desc.params.labels;
    const b = data.brepIds[face];
    const bits: string[] = [];
    for (let s = 0; s < labels.length; s++) {
      if ((data.membership[face] >>> s) & 1) bits.push(labels[s]);
    }
    const label = faceLabel(b, data.brepDesc);
    lines.push(`brep face: ${label}${label.includes('.') ? ' (split piece)' : ''}`);
    lines.push(`machinable in: ${bits.join(', ') || 'nothing (unmachinable)'}`);
    if (b >= data.valid.length) {
      lines.push('assigned: pending — result predates this cut');
    } else {
      const cat = data.current[b];
      const catName = cat === 255
        ? `unmachinable region ${data.region[face] || ''}`
        : cat === 254 ? 'conflict / needs split' : labels[cat];
      const overridden = cat !== data.defaults[b] ? ' (override)' : '';
      lines.push(`assigned: ${catName}${overridden}`);
    }
  } catch {
    // no setups result — skip those lines
  }

  const source = currentSource(ctx.manifest, ctx.params);
  if (!source) return lines;
  const tip = currentTip(source, ctx.params);
  const d = ctx.directions[source.direction];
  const dot = ctx.normals[3 * face] * d[0] + ctx.normals[3 * face + 1] * d[1]
    + ctx.normals[3 * face + 2] * d[2];
  lines.push(`normal angle: ${((Math.acos(Math.min(1, Math.max(-1, dot))) * 180) / Math.PI).toFixed(2)}°`);
  const access = await faceAccess(ctx, source);
  if (access) lines.push(`accessible: ${access[face] ? 'yes' : 'NO (undercut)'}`);
  if (tip) {
    const gap = await vertexGap(ctx, source, tip);
    lines.push(`tip gap: ${[0, 1, 2].map((k) => gap[ctx.faces[3 * face + k]].toFixed(3)).join(' / ')} mm`);
  }
  const minStick = await vertexMinStickout(ctx, source, tip);
  if (minStick) {
    lines.push(`required stickout${minStick.approx ? ' (approx)' : ''}: `
      + `${[0, 1, 2].map((k) => minStick.values[ctx.faces[3 * face + k]].toFixed(1)).join(' / ')} mm`);
  }
  return lines;
}

export const cncPlugin: ProcessPlugin = {
  processId: 'cnc',
  label: 'CNC machining',
  modes: [setupsMode, unifiedMode, accessMode, classMode, gapMode,
          stickoutMode, thinSpanMode, brepFacesMode, highlightsMode],
  defaults: () => ({
    source: 0,
    tip: 0,
    tolerance: 0.1,
    stickout: '',
    holder: '',
    scale: '',
    maxSpanRatio: 5.0,
    spanScale: '',
    rule: 'all',
    wallTol: 1.0,
    sideMill: true,
    mask: true,
    setupsResult: 0,
    setupsOption: 0,
    showLines: true,
    showArrows: true,
    splitMode: false,
    splitFace: null,
    splitStart: null,
  }),
  Controls: CncControls,
  inspect,
  onPick(face, point, ctx) {
    const { modeId, viewerParams } = useStore.getState();
    const params = viewerParams.cnc ?? {};
    if (modeId === 'setups' && params.splitMode) {
      return handleSplitPick(cncSplitHost, face, point, ctx);
    }
    return false;
  },
};
