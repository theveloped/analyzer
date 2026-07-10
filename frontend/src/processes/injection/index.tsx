// Injection molding plugin: parting-direction coverage plus rolling-sphere
// wall thickness and gaps/clearance heatmaps over generic result fields.

import { COL, heatmapMode, highlightsMode, paintMask } from '../../colorizers/core';
import type { Manifest } from '../../api/types';
import type { PaintInfo, ProcessPlugin, ViewCtx, ViewMode } from '../../registry/types';
import { useStore } from '../../state/store';

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

const coverageMode: ViewMode = {
  id: 'coverage',
  label: 'Parting option coverage',
  async paint(ctx): Promise<PaintInfo> {
    const results = resultsFor(ctx.manifest, 'parting_directions');
    const result = results[ctx.params.result ?? 0] ?? results[0];
    if (!result) {
      throw new Error('no parting_directions result yet — run the analysis below');
    }
    const fieldId = result.fields[ctx.params.option ?? 0] ?? result.fields[0];
    const desc = ctx.manifest.fields.find((f) => f.id === fieldId);
    if (!desc) throw new Error('coverage mask missing from the manifest');
    const mask = await ctx.getField(desc);
    const info = paintMask(ctx, mask, COL.floor, COL.tip,
      'covered by the selected parting set', 'not covered (needs a slide / redesign)');
    const option = result.stats.options?.[ctx.params.option ?? 0];
    if (option) {
      info.stats = `directions [${option.directions.join(', ')}] — `
        + `coverage ${(option.coverage * 100).toFixed(1)}%\n${info.stats}`;
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

  const results = manifest ? resultsFor(manifest, 'parting_directions') : [];
  const result = results[params.result ?? 0] ?? results[0];
  const options: { directions: number[]; coverage: number }[] = result?.stats.options ?? [];

  return (
    <>
      {modeId === 'coverage' && (
        <>
          <label>Result (parameter set)</label>
          <select value={params.result ?? 0} onChange={(e) => set('result', parseInt(e.target.value))}>
            {results.map((r, i) => (
              <option key={r.hash} value={i}>
                {`slides ${r.params.slides ?? 0} · ${r.hash}`}
              </option>
            ))}
            {!results.length && <option value={0}>no results yet</option>}
          </select>

          <label>Parting option</label>
          <select value={params.option ?? 0} onChange={(e) => set('option', parseInt(e.target.value))}>
            {options.slice(0, result?.fields.length ?? 0).map((o, i) => (
              <option key={i} value={i}>
                {`[${o.directions.join(', ')}] — ${(o.coverage * 100).toFixed(1)}%`}
              </option>
            ))}
            {!options.length && <option value={0}>—</option>}
          </select>

          {options.length > 0 && (
            <div className="hint">
              all ranked options:{' '}
              {options.map((o) => `[${o.directions.join(',')}] ${(o.coverage * 100).toFixed(1)}%`).join(' · ')}
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
  modes: [coverageMode, thicknessMode, gapsMode, highlightsMode],
  defaults: () => ({
    result: 0, option: 0,
    minThickness: 1.0, thicknessScale: '',
    minGap: 0.5, gapScale: '',
  }),
  Controls: InjectionControls,
  inspect,
};
