import { highlightsMode } from '../../colorizers/core';
import type { ProcessPlugin, ViewCtx } from '../../registry/types';
import { faceAccess, vertexGap, vertexMinStickout } from './compose';
import { CncControls } from './Controls';
import {
  accessMode, classMode, diffMode, gapMode, stickoutMode, unifiedMode,
} from './modes';
import { currentSource, currentTip } from './sources';

async function inspect(face: number, ctx: ViewCtx): Promise<string[]> {
  const source = currentSource(ctx.manifest, ctx.params);
  if (!source) return [];
  const tip = currentTip(source, ctx.params);
  const lines: string[] = [];
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
  modes: [unifiedMode, accessMode, classMode, gapMode, stickoutMode, diffMode, highlightsMode],
  defaults: () => ({
    source: 0,
    tip: 0,
    tolerance: 0.1,
    stickout: '',
    holder: '',
    scale: '',
    rule: 'all',
    wallTol: 1.0,
    sideMill: true,
    mask: true,
  }),
  Controls: CncControls,
  inspect,
};
