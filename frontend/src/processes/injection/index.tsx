// Injection molding plugin: mold-orientation assignment view (per-face side
// assignment with parting lines and direction arrows), rolling-sphere wall
// thickness and gaps/clearance heatmaps.

import {
  brepFacesMode, COL, heatmapMode, highlightsMode, paintCategory,
} from '../../colorizers/core';
import type { Manifest } from '../../api/types';
import type { PaintInfo, ProcessPlugin, RGB, ViewCtx, ViewMode } from '../../registry/types';
import { useStore } from '../../state/store';

const ARROW_COLORS: Record<string, RGB> = {
  main_a: [0.44, 0.64, 0.86], // side A
  main_b: [0.62, 0.8, 0.58], // side B
};

function resultsFor(manifest: Manifest, analysis: string) {
  return manifest.results.filter(
    (r) => r.process === 'injection_molding' && r.analysis === analysis);
}

/** Latest stored result's field descriptor for one npz member. */
function scalarField(ctx: ViewCtx, analysis: string, member: string) {
  const results = resultsFor(ctx.manifest, analysis);
  const result = results[results.length - 1];
  const fieldId = result?.fields.find((f) => f.endsWith(`.${member}`));
  return fieldId ? ctx.manifest.fields.find((f) => f.id === fieldId) ?? null : null;
}

function resolveField(ctx: ViewCtx, result: any, member: string) {
  const fieldId = result.fields.find((f: string) => f.endsWith(`.${member}`));
  return fieldId ? ctx.manifest.fields.find((f) => f.id === fieldId) ?? null : null;
}

const assignmentMode: ViewMode = {
  id: 'assignment',
  label: 'Mold orientation assignment',
  async paint(ctx): Promise<PaintInfo> {
    const results = resultsFor(ctx.manifest, 'mold_orientation');
    const result = results[ctx.params.result ?? 0] ?? results[results.length - 1];
    if (!result) {
      throw new Error('no mold_orientation result yet — run the analysis below');
    }
    const option = ctx.params.option ?? 0;
    const variant = ctx.params.display ?? 'resolved';
    const desc = resolveField(ctx, result, `${variant}_${option}`);
    if (!desc) {
      throw new Error(variant === 'brep'
        ? 'no BREP assignment stored — re-mesh the part from STEP, then re-run'
        : 'assignment field missing — re-run mold_orientation');
    }
    const values = await ctx.getField(desc) as Uint8Array;
    const info = paintCategory(ctx, values, desc.params.labels, desc.params.colors);

    if (ctx.params.showLines !== false) {
      const linesDesc = resolveField(ctx, result, `parting_lines_${option}`);
      if (linesDesc) ctx.setLines(await ctx.getField(linesDesc) as Float32Array);
    }
    const opt = result.stats.options?.[option];
    if (ctx.params.showArrows !== false && opt) {
      ctx.setArrows(opt.arrows.map((arrow: any) => ({
        direction: arrow.direction,
        color: arrow.kind === 'slide'
          ? desc.params.colors[4 + arrow.index] ?? COL.holder
          : ARROW_COLORS[arrow.kind] ?? COL.ok,
      })));
    }

    if (opt) {
      const slides = opt.slides.length
        ? ` [${opt.slides.map((s: any) => `d${s.direction} +${s.marginal}`).join(', ')}]`
        : '';
      info.stats = `${opt.feasible ? 'FEASIBLE' : 'infeasible'}`
        + ` · coverage ${(opt.coverage * 100).toFixed(1)}%`
        + ` · ${opt.slides.length} slide(s)${slides}`
        + ` · internal ${opt.counts.internal}`;
    }
    return info;
  },
};

const thicknessMode = heatmapMode(
  'thickness', 'Wall thickness heatmap',
  (ctx) => scalarField(ctx, 'thickness', 'thickness'),
  {
    flagDirection: 'below',
    thresholdParam: 'minThickness',
    scaleParam: 'thicknessScale',
    okLabel: 'thick — ok',
  });

const gapsMode = heatmapMode(
  'gaps', 'Wall gaps / clearance heatmap',
  (ctx) => scalarField(ctx, 'gaps', 'gap'),
  {
    flagDirection: 'below',
    thresholdParam: 'minGap',
    scaleParam: 'gapScale',
    okLabel: 'clearance ok (incl. no opposing wall in range)',
  });

async function inspect(face: number, ctx: ViewCtx): Promise<string[]> {
  const lines: string[] = [];
  const at3 = (field: Float32Array) =>
    [0, 1, 2].map((k) => field[ctx.faces[3 * face + k]].toFixed(2)).join(' / ');

  const results = resultsFor(ctx.manifest, 'mold_orientation');
  const result = results[ctx.params.result ?? 0] ?? results[results.length - 1];
  if (result) {
    const option = ctx.params.option ?? 0;
    const variant = ctx.params.display ?? 'resolved';
    const desc = resolveField(ctx, result, `${variant}_${option}`);
    if (desc) {
      const values = await ctx.getField(desc) as Uint8Array;
      lines.push(`assignment: ${desc.params.labels[values[face]] ?? values[face]}`);
    }
  }
  const brep = ctx.manifest.fields.find((f) => f.id === 'brep_faces');
  if (brep) {
    const ids = await ctx.getField(brep) as Uint32Array;
    lines.push(`brep face: ${ids[face]}`);
  }
  const thickness = scalarField(ctx, 'thickness', 'thickness');
  if (thickness) {
    lines.push(`wall thickness: ${at3(await ctx.getField(thickness) as Float32Array)} mm`);
  }
  const gap = scalarField(ctx, 'gaps', 'gap');
  if (gap) {
    lines.push(`wall gap: ${at3(await ctx.getField(gap) as Float32Array)} mm`);
  }
  return lines;
}

const EMPTY: Record<string, any> = {};

function NumberParam({ label, value, placeholder, onChange }: {
  label: string; value: any; placeholder?: string; onChange: (v: string) => void;
}) {
  return (
    <div>
      <label>{label}</label>
      <input
        type="number" step="0.1" value={value} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function InjectionControls() {
  const manifest = useStore((s) => s.manifest);
  const modeId = useStore((s) => s.modeId);
  const params = useStore((s) => s.viewerParams.injection_molding) ?? EMPTY;
  const setParam = useStore((s) => s.setViewerParam);
  const set = (name: string, value: any) => setParam('injection_molding', name, value);

  const results = manifest ? resultsFor(manifest, 'mold_orientation') : [];
  const result = results[params.result ?? 0] ?? results[results.length - 1];
  const options: any[] = result?.stats.options ?? [];
  const fieldOptions = options.slice(0, 3);

  return (
    <>
      {modeId === 'assignment' && (
        <>
          <label>Result (parameter set)</label>
          <select value={params.result ?? 0} onChange={(e) => set('result', parseInt(e.target.value))}>
            {results.map((r, i) => (
              <option key={r.hash} value={i}>
                {`max slides ${r.params.max_slides ?? '?'} · ${r.hash}`}
              </option>
            ))}
            {!results.length && <option value={0}>no results yet</option>}
          </select>

          <label>Orientation option</label>
          <select value={params.option ?? 0} onChange={(e) => set('option', parseInt(e.target.value))}>
            {fieldOptions.map((o, i) => (
              <option key={i} value={i}>
                {`±d${o.pair[0]} · ${o.slides.length} slide(s) · ${o.feasible ? 'feasible' : 'infeasible'}`}
              </option>
            ))}
            {!fieldOptions.length && <option value={0}>—</option>}
          </select>

          <label>Display</label>
          <select value={params.display ?? 'resolved'} onChange={(e) => set('display', e.target.value)}>
            <option value="band">band (either faces explicit)</option>
            <option value="resolved">resolved (auto-assigned)</option>
            <option value="brep">whole BREP faces</option>
          </select>

          <div className="row">
            <label className="check">
              <input
                type="checkbox" checked={params.showLines !== false}
                onChange={(e) => set('showLines', e.target.checked)}
              />
              parting lines
            </label>
            <label className="check">
              <input
                type="checkbox" checked={params.showArrows !== false}
                onChange={(e) => set('showArrows', e.target.checked)}
              />
              direction arrows
            </label>
          </div>

          {options.length > 0 && (
            <div className="hint">
              ranked: {options.map((o, i) =>
                `#${i} ±d${o.pair[0]} ${o.feasible ? '✓' : '✗'} ${(o.coverage * 100).toFixed(0)}%`).join(' · ')}
            </div>
          )}
        </>
      )}

      {modeId === 'thickness' && (
        <div className="row">
          <NumberParam
            label="Min thickness (mm)" value={params.minThickness ?? 1.0}
            onChange={(v) => set('minThickness', v)}
          />
          <NumberParam
            label="Heatmap max (mm)" value={params.thicknessScale ?? ''}
            placeholder="auto" onChange={(v) => set('thicknessScale', v)}
          />
        </div>
      )}

      {modeId === 'gaps' && (
        <div className="row">
          <NumberParam
            label="Min gap (mm)" value={params.minGap ?? 0.5}
            onChange={(v) => set('minGap', v)}
          />
          <NumberParam
            label="Heatmap max (mm)" value={params.gapScale ?? ''}
            placeholder="auto" onChange={(v) => set('gapScale', v)}
          />
        </div>
      )}
    </>
  );
}

export const injectionPlugin: ProcessPlugin = {
  processId: 'injection_molding',
  label: 'Injection molding',
  modes: [assignmentMode, thicknessMode, gapsMode, brepFacesMode, highlightsMode],
  defaults: () => ({
    result: 0, option: 0, display: 'resolved',
    showLines: true, showArrows: true,
    minThickness: 1.0, thicknessScale: '',
    minGap: 0.5, gapScale: '',
  }),
  Controls: InjectionControls,
  inspect,
};
