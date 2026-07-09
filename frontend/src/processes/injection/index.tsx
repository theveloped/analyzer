// Injection molding plugin: parting-direction coverage over the shared
// accessibility matrix. Skeleton showing how a non-CNC process plugs in —
// one custom mode over generic mask painting plus a result/option picker.

import { COL, highlightsMode, paintMask } from '../../colorizers/core';
import type { PaintInfo, ProcessPlugin, ViewCtx, ViewMode } from '../../registry/types';
import { useStore } from '../../state/store';

function partingResults(ctx: ViewCtx) {
  return ctx.manifest.results.filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'parting_directions');
}

const coverageMode: ViewMode = {
  id: 'coverage',
  label: 'Parting option coverage',
  async paint(ctx): Promise<PaintInfo> {
    const results = partingResults(ctx);
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

const EMPTY: Record<string, any> = {};

function InjectionControls() {
  const manifest = useStore((s) => s.manifest);
  const params = useStore((s) => s.viewerParams.injection_molding) ?? EMPTY;
  const setParam = useStore((s) => s.setViewerParam);
  const set = (name: string, value: any) => setParam('injection_molding', name, value);

  const results = (manifest?.results ?? []).filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'parting_directions');
  const result = results[params.result ?? 0] ?? results[0];
  const options: { directions: number[]; coverage: number }[] = result?.stats.options ?? [];

  return (
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
  );
}

export const injectionPlugin: ProcessPlugin = {
  processId: 'injection_molding',
  label: 'Injection molding',
  modes: [coverageMode, highlightsMode],
  defaults: () => ({ result: 0, option: 0 }),
  Controls: InjectionControls,
};
