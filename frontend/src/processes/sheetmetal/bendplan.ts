// Bend plan view: panels painted per rigid panel, the flat layout beside
// the part with bend axes and REQUIRED (green) / FORBIDDEN (red) tooling
// intervals, and the ranked plan summary from the search.

import type { FieldDescriptor, ResultEntry } from '../../api/types';
import { COL, segmentIdColor } from '../../colorizers/core';
import type {
  LegendEntry, RGB, ViewCtx, ViewMode,
} from '../../registry/types';

// keep in sync with BENDPLAN_SCHEMA in processes/sheet_metal.py
export const BENDPLAN_SCHEMA = 2;

const OUTLINE: RGB = [0.7, 0.7, 0.75];
const AXES: RGB = [0.95, 0.66, 0.23];
const REQUIRED: RGB = [0.4, 0.78, 0.42];
const FORBIDDEN: RGB = [0.88, 0.29, 0.23];

export function latestBendPlan(ctx: ViewCtx): ResultEntry | null {
  const results = ctx.manifest.results.filter((r) => r.process === 'sheet_metal'
    && r.analysis === 'bend_plan' && !r.stale
    && r.params.schema === BENDPLAN_SCHEMA);
  return results.length ? results[results.length - 1] : null;
}

export function planField(
  ctx: ViewCtx, result: ResultEntry, name: string,
): FieldDescriptor | null {
  return ctx.manifest.fields.find(
    (f) => f.id === `results.sheet_metal.bend_plan.${result.hash}.${name}`,
  ) ?? null;
}

export const bendPlanMode: ViewMode = {
  id: 'bend_plan',
  label: 'Bend plan (press brake)',
  async paint(ctx) {
    const result = latestBendPlan(ctx);
    if (!result) {
      throw new Error('no bend plan result — run sheet_metal/bend_plan in the Compute panel (needs prep/aag)');
    }
    const s = result.stats;

    // paint the mesh per rigid panel
    const panelDesc = planField(ctx, result, 'panel_id');
    if (panelDesc) {
      const panels = await ctx.getField(panelDesc) as Uint8Array;
      ctx.paintFaces((f) => (panels[f]
        ? segmentIdColor(panels[f]) : COL.inaccess));
    }
    ctx.setMeshOpacity(0.4);

    // lay the flat model out beside the part
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
    const offset: [number, number, number] = [
      maxX + Math.max(5, 0.1 * (maxX - minX)), minY, minZ];

    const draw = async (name: string, color: RGB) => {
      const desc = planField(ctx, result, name);
      if (!desc) return 0;
      const segments = await ctx.getField(desc) as Float32Array;
      if (!segments.length) return 0;
      const moved = new Float32Array(segments.length);
      for (let i = 0; i < segments.length; i += 3) {
        moved[i] = segments[i] + offset[0];
        moved[i + 1] = segments[i + 1] + offset[1];
        moved[i + 2] = segments[i + 2] + offset[2];
      }
      ctx.setLines(moved, color, false);
      return segments.length / 6;
    };
    await draw('outline_lines', OUTLINE);
    await draw('bend_axis_lines', AXES);
    const required = await draw('required_lines', REQUIRED);
    const forbidden = await draw('forbidden_lines', FORBIDDEN);

    const legend: LegendEntry[] = [
      { color: OUTLINE, label: `panel outlines (${s.panel_count} panels)` },
      { color: AXES, label: `bend axes (${s.bend_count} bends)` },
      ...(required ? [{ color: REQUIRED, label: 'required tooling span' }] : []),
      ...(forbidden ? [{ color: FORBIDDEN, label: 'forbidden (collision) span' }] : []),
    ];

    const best = (s.plans ?? [])[0];
    const planSummary = best
      ? ` · best plan: ${best.setups.length} setup(s), `
        + `${best.objective[2]} sections, ${best.objective[3].toFixed(0)} mm `
        + `installed (${best.setups.map((x: any) => `${x.punch_id}/${x.die_id}`).join(', ')})`
      : '';
    const warnings = (s.warnings ?? []).length
      ? ` — ⚠ ${s.warnings.join('; ')}` : '';
    return {
      legend,
      stats: `${s.feasible ? 'feasible ✓' : 'NOT feasible ✗'} · `
        + `${s.bend_count} bends in ${s.sister_group_count} group(s) · `
        + `t ${Number(s.thickness).toFixed(2)} mm · ${s.machine}`
        + planSummary + warnings,
    };
  },
};

/** Inspect line for the clicked face: its rigid panel + owning bends. */
export async function inspectBendPlan(
  face: number, ctx: ViewCtx,
): Promise<string[]> {
  const result = latestBendPlan(ctx);
  if (!result) return [];
  try {
    const desc = planField(ctx, result, 'panel_id');
    if (!desc) return [];
    const panels = await ctx.getField(desc) as Uint8Array;
    if (!panels[face]) return [];
    const panelId = panels[face] - 1;
    const bends = (result.stats.graph?.bends ?? [])
      .filter((b: any) => b.parent_panel === panelId
        || b.child_panel === panelId)
      .map((b: any) => b.id);
    return [`bend plan: panel ${panelId}`
      + (bends.length ? ` (bends ${bends.join(', ')})` : '')];
  } catch {
    return [];
  }
}
