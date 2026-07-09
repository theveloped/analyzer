// Viewer-side CNC controls: direction/engine cache, tool tip and the live
// thresholds. Every change repaints client-side from cached fields.

import { useStore } from '../../state/store';
import { cncSources } from './sources';

const EMPTY: Record<string, any> = {};

export function CncControls() {
  const manifest = useStore((s) => s.manifest);
  const modeId = useStore((s) => s.modeId);
  const params = useStore((s) => s.viewerParams.cnc) ?? EMPTY;
  const setParam = useStore((s) => s.setViewerParam);
  const set = (name: string, value: any) => setParam('cnc', name, value);

  const sources = manifest ? cncSources(manifest) : [];
  const source = sources[params.source] ?? sources[0] ?? null;
  const showScale = modeId === 'gap' || modeId === 'stickout';

  return (
    <>
      <label>Direction / engine</label>
      <select
        value={params.source ?? 0}
        onChange={(e) => { set('source', parseInt(e.target.value)); set('tip', 0); }}
      >
        {sources.map((s, i) => {
          const d = manifest!.directions[s.direction]?.map((x) => x.toFixed(2)).join(', ');
          return <option key={s.key} value={i}>{`dir ${s.direction} [${d}] — ${s.engine}`}</option>;
        })}
        {!sources.length && <option value={0}>no cached fields</option>}
      </select>

      <label>Tool tip (diameter × corner radius)</label>
      <select value={params.tip ?? 0} onChange={(e) => set('tip', parseInt(e.target.value))}>
        {(source?.tips ?? []).map((t, i) => {
          const kind = t.corner_radius === 0 ? 'flat'
            : (t.corner_radius >= t.diameter / 2 ? 'ball' : 'bull');
          return <option key={i} value={i}>{`D${t.diameter} rc${t.corner_radius} (${kind})`}</option>;
        })}
        {!source?.tips.length && <option value={0}>no tip fields</option>}
      </select>

      <div className="row">
        <div>
          <label>Tolerance (mm)</label>
          <input
            type="number" min={0} step={0.05} value={params.tolerance ?? 0.1}
            onChange={(e) => set('tolerance', e.target.value)}
          />
        </div>
        <div>
          <label>Stickout (mm)</label>
          <input
            type="number" placeholder="none" value={params.stickout ?? ''}
            onChange={(e) => set('stickout', e.target.value)}
          />
        </div>
      </div>

      <label>Holder cylinders (radius:start, …)</label>
      <input
        type="text" placeholder="e.g. 3:0, 8:40" value={params.holder ?? ''}
        onChange={(e) => set('holder', e.target.value)}
      />

      {showScale && (
        <div>
          <label>Heatmap max (mm)</label>
          <input
            type="number" placeholder="auto" value={params.scale ?? ''}
            onChange={(e) => set('scale', e.target.value)}
          />
          <div className="ramp" />
        </div>
      )}

      <label>Face rule</label>
      <select value={params.rule ?? 'all'} onChange={(e) => set('rule', e.target.value)}>
        <option value="all">blocked if all 3 vertices blocked (engine rule)</option>
        <option value="any">blocked if any vertex blocked</option>
      </select>

      <div className="row">
        <div>
          <label>Wall tolerance (°)</label>
          <input
            type="number" min={0} step={0.5} value={params.wallTol ?? 1.0}
            onChange={(e) => set('wallTol', e.target.value)}
          />
        </div>
        <div>
          <label className="check">
            <input
              type="checkbox" checked={params.sideMill ?? true}
              onChange={(e) => set('sideMill', e.target.checked)}
            />
            walls side-milled
          </label>
        </div>
      </div>

      <label className="check">
        <input
          type="checkbox" checked={params.mask ?? true}
          onChange={(e) => set('mask', e.target.checked)}
        />
        grey out inaccessible faces
      </label>
    </>
  );
}
