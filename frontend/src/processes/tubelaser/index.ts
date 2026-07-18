// Tube / profile laser plugin: shell roles and the unrolled cut pattern
// from the tube_laser/profile result.

import type { ResultEntry } from '../../api/types';
import {
  brepFacesMode, COL, faceAttrsMode, highlightsMode, paintCategory,
} from '../../colorizers/core';
import type {
  ProcessPlugin, RGB, ViewCtx, ViewMode,
} from '../../registry/types';

// keep in sync with TUBE_SCHEMA in processes/tube_laser.py
export const TUBE_SCHEMA = 2;

const ROLE_LABELS = ['other', 'outer shell', 'inner shell', 'end cut'];
const ROLE_COLORS: RGB[] = [
  COL.ok,
  [0.44, 0.64, 0.86],   // outer shell
  [0.62, 0.8, 0.58],    // inner shell
  [0.95, 0.66, 0.23],   // end cut
];

function latestProfile(ctx: ViewCtx): ResultEntry | null {
  const results = ctx.manifest.results.filter((r) => r.process === 'tube_laser'
    && r.analysis === 'profile' && !r.stale
    && r.params.schema === TUBE_SCHEMA);
  return results.length ? results[results.length - 1] : null;
}

async function profileField(ctx: ViewCtx, result: ResultEntry, name: string) {
  const desc = ctx.manifest.fields.find(
    (f) => f.id === `results.tube_laser.profile.${result.hash}.${name}`);
  return desc ? ctx.getField(desc) : null;
}

function sectionLine(result: ResultEntry): string {
  const s = result.stats;
  if (s.verdict === 'none') return `not a straight profile — ${(s.reasons ?? []).join('; ')}`;
  return `${s.verdict} ${Number(s.width).toFixed(1)} × ${Number(s.height).toFixed(1)}`
    + ` × t${Number(s.thickness).toFixed(2)} mm · length ${Number(s.length).toFixed(1)} mm`
    + ` · corner r${Number(s.inner_radius).toFixed(1)}/${Number(s.outer_radius).toFixed(1)}`;
}

const rolesMode: ViewMode = {
  id: 'tube_roles',
  label: 'Shell roles',
  async paint(ctx) {
    const result = latestProfile(ctx);
    if (!result) {
      throw new Error('no profile result — run tube_laser/profile in the Compute panel (needs prep/aag)');
    }
    const roles = await profileField(ctx, result, 'face_role') as Uint8Array;
    if (!roles) throw new Error('profile result carries no role field — re-run it');
    const info = paintCategory(ctx, roles, ROLE_LABELS, ROLE_COLORS);
    return { legend: info.legend, stats: sectionLine(result) };
  },
};

const cutPatternMode: ViewMode = {
  id: 'cut_pattern',
  label: 'Cut pattern (unrolled)',
  async paint(ctx) {
    const result = latestProfile(ctx);
    if (!result) {
      throw new Error('no profile result — run tube_laser/profile in the Compute panel (needs prep/aag)');
    }
    if (!result.stats.flat_size) {
      throw new Error('no unroll stored — re-run tube_laser/profile with unroll enabled');
    }
    const roles = await profileField(ctx, result, 'face_role') as Uint8Array | null;
    if (roles) {
      ctx.paintFaces((f) => ROLE_COLORS[roles[f]] ?? COL.ok);
    }
    ctx.setMeshOpacity(0.35);

    let maxX = -Infinity;
    let minX = Infinity;
    let minY = Infinity;
    let minZ = Infinity;
    for (let i = 0; i < ctx.verts.length; i += 3) {
      if (ctx.verts[i] > maxX) maxX = ctx.verts[i];
      if (ctx.verts[i] < minX) minX = ctx.verts[i];
      if (ctx.verts[i + 1] < minY) minY = ctx.verts[i + 1];
      if (ctx.verts[i + 2] < minZ) minZ = ctx.verts[i + 2];
    }
    const offset = [maxX + Math.max(5, 0.1 * (maxX - minX)), minY, minZ];

    const draw = async (name: string, color: RGB) => {
      const segments = await profileField(ctx, result, name) as Float32Array | null;
      if (!segments || !segments.length) return 0;
      const moved = new Float32Array(segments.length);
      for (let i = 0; i < segments.length; i += 3) {
        moved[i] = segments[i] + offset[0];
        moved[i + 1] = segments[i + 1] + offset[1];
        moved[i + 2] = segments[i + 2] + offset[2];
      }
      ctx.setLines(moved, color, false);
      return segments.length / 6;
    };
    await draw('outline_lines', [0.92, 0.92, 0.95]);
    const holes = await draw('hole_lines', [0.44, 0.64, 0.86]);

    const s = result.stats;
    return {
      legend: [
        { color: [0.92, 0.92, 0.95] as RGB, label: 'unrolled shell' },
        ...(holes ? [{ color: [0.44, 0.64, 0.86] as RGB, label: `cutouts (${s.hole_count})` }] : []),
      ],
      stats: `${sectionLine(result)} · pattern ${s.flat_size[0].toFixed(1)} × ${s.flat_size[1].toFixed(1)} mm`,
    };
  },
};

async function inspect(face: number, ctx: ViewCtx): Promise<string[]> {
  const result = latestProfile(ctx);
  if (!result) return [];
  try {
    const roles = await profileField(ctx, result, 'face_role') as Uint8Array;
    return roles ? [`tube role: ${ROLE_LABELS[roles[face]] ?? 'unknown'}`] : [];
  } catch {
    return [];
  }
}

export const tubeLaserPlugin: ProcessPlugin = {
  processId: 'tube_laser',
  label: 'Tube / profile laser',
  modes: [rolesMode, cutPatternMode, faceAttrsMode, brepFacesMode,
          highlightsMode],
  defaults: () => ({}),
  inspect,
};
