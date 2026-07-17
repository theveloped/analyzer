// Sheet metal plugin: face roles + bend radius from the sheet_metal/detect
// result; flat pattern rendering arrives with the unfold analysis.

import type { ResultEntry } from '../../api/types';
import {
  brepFacesMode, COL, faceAttrsMode, FocusTracker, highlightsMode,
  paintCategory, rampColor,
} from '../../colorizers/core';
import type {
  ProcessPlugin, RGB, ViewCtx, ViewMode,
} from '../../registry/types';

// keep in sync with SHEET_SCHEMA in processes/sheet_metal.py
export const SHEET_SCHEMA = 2;

const ROLE_LABELS = ['other', 'base skin', 'opposite skin', 'bend', 'wall / cut edge', 'feature'];
const ROLE_COLORS: RGB[] = [
  COL.ok,                // other
  [0.44, 0.64, 0.86],    // base skin
  [0.62, 0.8, 0.58],     // opposite skin
  [0.95, 0.66, 0.23],    // bend
  [0.55, 0.5, 0.62],     // wall / cut edge
  [0.88, 0.29, 0.23],    // feature (embossing / extrusion / chamfer)
];

export function latestSheet(ctx: ViewCtx, analysis: string): ResultEntry | null {
  const results = ctx.manifest.results.filter((r) => r.process === 'sheet_metal'
    && r.analysis === analysis && !r.stale && r.params.schema === SHEET_SCHEMA);
  return results.length ? results[results.length - 1] : null;
}

async function sheetField(
  ctx: ViewCtx, result: ResultEntry, name: string,
): Promise<Uint8Array | Float32Array> {
  const desc = ctx.manifest.fields.find(
    (f) => f.id === `results.sheet_metal.${result.analysis}.${result.hash}.${name}`);
  if (!desc) throw new Error(`sheet field ${name} missing — re-run the analysis`);
  return await ctx.getField(desc) as Uint8Array | Float32Array;
}

function verdictLine(result: ResultEntry): string {
  const s = result.stats;
  const reasons = (s.reasons ?? []).length ? ` — ${s.reasons.join('; ')}` : '';
  const features = (s.features ?? []).length
    ? ` · ${s.features.length} skin features` : '';
  const warnings = (s.warnings ?? []).length
    ? ` — ⚠ ${s.warnings.join('; ')}` : '';
  return `${s.verdict} · thickness ${Number(s.thickness).toFixed(2)} mm · `
    + `${s.bend_count} bends${features}${reasons}${warnings}`;
}

const rolesMode: ViewMode = {
  id: 'sheet_roles',
  label: 'Sheet face roles',
  async paint(ctx) {
    const result = latestSheet(ctx, 'detect');
    if (!result) {
      throw new Error('no sheet detection result — run sheet_metal/detect in the Compute panel (needs prep/aag)');
    }
    const roles = await sheetField(ctx, result, 'face_role') as Uint8Array;
    const info = paintCategory(ctx, roles, ROLE_LABELS, ROLE_COLORS);
    return { legend: info.legend, stats: verdictLine(result) };
  },
};

const bendRadiusMode: ViewMode = {
  id: 'bend_radius',
  label: 'Bend radius',
  async paint(ctx) {
    const result = latestSheet(ctx, 'detect');
    if (!result) {
      throw new Error('no sheet detection result — run sheet_metal/detect in the Compute panel (needs prep/aag)');
    }
    const radius = await sheetField(ctx, result, 'bend_radius') as Float32Array;
    // rule of thumb: inner bend radius below the sheet thickness risks
    // cracking — flag those, ramp the rest
    const thickness = Number(result.stats.thickness) || 1;
    let max = 0;
    for (let f = 0; f < ctx.faceCount; f++) {
      if (isFinite(radius[f]) && radius[f] > max) max = radius[f];
    }
    max = Math.max(max, thickness * 2, 1e-6);
    const tracker = new FocusTracker(ctx);
    let tight = 0;
    ctx.paintFaces((f) => {
      const r = radius[f];
      if (!isFinite(r)) return COL.ok;
      if (r < thickness) {
        tight++;
        tracker.add('tight', f);
        return COL.tip;
      }
      tracker.add('bend', f);
      return rampColor(0.15 + 0.7 * (1 - Math.min(1, r / max)));
    });
    return {
      legend: [
        { color: COL.tip, label: `radius < thickness (${thickness.toFixed(1)} mm) — cracking risk`, focus: tracker.focus('tight') },
        { color: rampColor(0.5), label: 'bend radius (tighter = warmer)', focus: tracker.focus('bend') },
        { color: COL.ok, label: 'not a bend' },
      ],
      stats: `${verdictLine(result)}${tight ? ` · ${tight} tight bend faces` : ''}`,
    };
  },
};

async function inspect(face: number, ctx: ViewCtx): Promise<string[]> {
  const result = latestSheet(ctx, 'detect');
  if (!result) return [];
  try {
    const roles = await sheetField(ctx, result, 'face_role') as Uint8Array;
    const radius = await sheetField(ctx, result, 'bend_radius') as Float32Array;
    const lines = [`sheet role: ${ROLE_LABELS[roles[face]] ?? 'unknown'}`];
    if (isFinite(radius[face])) {
      lines.push(`bend radius: ${radius[face].toFixed(2)} mm`);
    }
    // resolve the clicked face to a recognized skin feature, if any
    const brepDesc = ctx.manifest.fields.find((f) => f.id === 'brep_faces');
    if (brepDesc && (result.stats.features ?? []).length) {
      const ids = await ctx.getField(brepDesc) as Uint32Array;
      const feature = (result.stats.features as any[]).find(
        (f) => f.faces.includes(ids[face]));
      if (feature) {
        lines.push(`feature: ${feature.type} ${feature.value.toFixed(2)} mm`
          + (feature.side ? ` (${feature.side} side)` : ''));
      }
    }
    return lines;
  } catch {
    return [];
  }
}

import { SheetMetalControls } from './Controls';
import { patternMode } from './pattern';

export const sheetMetalPlugin: ProcessPlugin = {
  processId: 'sheet_metal',
  label: 'Sheet metal',
  modes: [patternMode, rolesMode, bendRadiusMode, faceAttrsMode,
          brepFacesMode, highlightsMode],
  defaults: () => ({}),
  Controls: SheetMetalControls,
  inspect,
};
