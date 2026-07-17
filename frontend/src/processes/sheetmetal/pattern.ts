// Flat-pattern view: the unfolded outline / holes / bend lines drawn as a
// 2D overlay laid out beside the part, over the role-painted (translucent)
// mesh.

import type { FieldDescriptor, ResultEntry } from '../../api/types';
import { COL } from '../../colorizers/core';
import type { RGB, ViewCtx, ViewMode } from '../../registry/types';
import { latestSheet } from './index';

const OUTLINE: RGB = [0.92, 0.92, 0.95];
const HOLES: RGB = [0.44, 0.64, 0.86];
const BENDS: RGB = [0.95, 0.66, 0.23];

function patternField(
  ctx: ViewCtx, result: ResultEntry, name: string,
): FieldDescriptor | null {
  return ctx.manifest.fields.find(
    (f) => f.id === `results.sheet_metal.flat_pattern.${result.hash}.${name}`,
  ) ?? null;
}

/** Translate flattened segment coordinates to sit beside the part. */
function place(segments: Float32Array, offset: [number, number, number]): Float32Array {
  const moved = new Float32Array(segments.length);
  for (let i = 0; i < segments.length; i += 3) {
    moved[i] = segments[i] + offset[0];
    moved[i + 1] = segments[i + 1] + offset[1];
    moved[i + 2] = segments[i + 2] + offset[2];
  }
  return moved;
}

export const patternMode: ViewMode = {
  id: 'flat_pattern',
  label: 'Flat pattern',
  async paint(ctx) {
    const result = latestSheet(ctx, 'flat_pattern');
    if (!result) {
      throw new Error('no flat pattern result — run sheet_metal/flat_pattern in the Compute panel (needs prep/aag)');
    }

    // translucent part painted by role for orientation
    const roleDesc = patternField(ctx, result, 'face_role');
    if (roleDesc) {
      const roles = await ctx.getField(roleDesc) as Uint8Array;
      const roleColors: RGB[] = [
        COL.ok, [0.44, 0.64, 0.86], [0.62, 0.8, 0.58],
        [0.95, 0.66, 0.23], [0.55, 0.5, 0.62],
      ];
      ctx.paintFaces((f) => roleColors[roles[f]] ?? COL.ok);
    } else {
      ctx.paintFaces(() => COL.ok);
    }
    ctx.setMeshOpacity(0.35);

    // part bounding box -> lay the pattern out beside it
    let maxX = -Infinity;
    let minY = Infinity;
    let minZ = Infinity;
    let minX = Infinity;
    for (let i = 0; i < ctx.verts.length; i += 3) {
      if (ctx.verts[i] > maxX) maxX = ctx.verts[i];
      if (ctx.verts[i] < minX) minX = ctx.verts[i];
      if (ctx.verts[i + 1] < minY) minY = ctx.verts[i + 1];
      if (ctx.verts[i + 2] < minZ) minZ = ctx.verts[i + 2];
    }
    const margin = Math.max(5, 0.1 * (maxX - minX));
    const offset: [number, number, number] = [maxX + margin, minY, minZ];

    const draw = async (name: string, color: RGB) => {
      const desc = patternField(ctx, result, name);
      if (!desc) return 0;
      const segments = await ctx.getField(desc) as Float32Array;
      if (!segments.length) return 0;
      ctx.setLines(place(segments, offset), color, false);
      return segments.length / 6;
    };
    const outline = await draw('outline_lines', OUTLINE);
    const holes = await draw('hole_lines', HOLES);
    const bends = await draw('bend_lines', BENDS);

    const s = result.stats;
    const legend = [
      { color: OUTLINE, label: `outer contour (${outline} segments)` },
      ...(holes ? [{ color: HOLES, label: `holes (${s.hole_count})` }] : []),
      ...(bends ? [{ color: BENDS, label: `bend lines (${s.bends.length})` }] : []),
    ];
    const bendSummary = (s.bends ?? []).map((b: any) => `${b.angle_deg.toFixed(0)}° r${b.inner_radius.toFixed(1)}`).join(', ');
    return {
      legend,
      stats: `t ${Number(s.thickness).toFixed(2)} mm · k ${s.k_factor} · `
        + `${s.flat_size[0].toFixed(1)} × ${s.flat_size[1].toFixed(1)} mm · `
        + `area ${Number(s.flat_area).toFixed(0)} mm² · volume error `
        + `${Number(s.volume_error_pct).toFixed(2)}% ${s.volume_ok ? '✓' : '✗'}`
        + `${s.developable ? '' : ` · NOT developable (${s.open_wires} open wires)`}`
        + (bendSummary ? ` · bends: ${bendSummary}` : ''),
    };
  },
};
