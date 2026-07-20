// Viewer-side directions controls (legacy viewer). The candidate set is a live
// client-side model — edits update the arrows instantly, no recompute.
// Accessibility is a separate, deferred concern and is never run from here.

import { useState } from 'react';
import { useStore } from '../../state/store';
import { useDirectionSetup } from './useSetup';

const num = (v: any) => { const n = parseFloat(v); return isFinite(n) ? n : NaN; };

export function DirectionsControls() {
  const manifest = useStore((s) => s.manifest);
  const { setup, patch, params, setParam } = useDirectionSetup();
  const setUi = (name: string, value: any) => setParam('directions', name, value);

  const [ax, setAx] = useState('0');
  const [ay, setAy] = useState('0');
  const [az, setAz] = useState('1');

  const picking = !!params.pickMode;
  const pendingBrep: number[] = params.pendingBrep ?? [];
  const holeN = manifest?.hole_candidates?.length ?? 0;

  function addAxis() {
    const v = [num(ax), num(ay), num(az)];
    if (v.some((c) => !isFinite(c)) || v.every((c) => c === 0)) return;
    patch({ manual: [...setup.manual, v] });
  }
  function addGroup() {
    if (!pendingBrep.length) return;
    patch({ brepGroups: [...setup.brepGroups, [...pendingBrep].sort((a, b) => a - b)] });
    setUi('pendingBrep', []);
    setUi('pickMode', false);
  }

  return (
    <>
      <label>Uniform sample count</label>
      <input
        type="number" min={0} step={1} value={setup.count}
        onChange={(e) => patch({ count: Math.max(0, parseInt(e.target.value) || 0) })}
      />

      <label className="check">
        <input type="checkbox" checked={setup.axes} onChange={(e) => patch({ axes: e.target.checked })} />
        world X / Y / Z axes
      </label>
      <label className="check">
        <input type="checkbox" checked={setup.bboxAxes} onChange={(e) => patch({ bboxAxes: e.target.checked })} />
        bounding-box (PCA) axes
      </label>
      <label className="check">
        <input type="checkbox" checked={setup.holeAxes} onChange={(e) => patch({ holeAxes: e.target.checked })} />
        hole / cylinder axes{holeN ? ` (${holeN})` : ''}
      </label>

      <label>Add manual axis (x / y / z)</label>
      <div className="row">
        <input type="number" step={0.1} value={ax} onChange={(e) => setAx(e.target.value)} />
        <input type="number" step={0.1} value={ay} onChange={(e) => setAy(e.target.value)} />
        <input type="number" step={0.1} value={az} onChange={(e) => setAz(e.target.value)} />
        <button type="button" onClick={addAxis}>Add</button>
      </div>

      <label>Averaged normal from BREP faces</label>
      <div className="row">
        <button
          type="button" className={picking ? 'active' : ''}
          onClick={() => { if (picking) setUi('pendingBrep', []); setUi('pickMode', !picking); }}
        >
          {picking ? `Picking… (${pendingBrep.length})` : 'Pick faces'}
        </button>
        <button type="button" disabled={!pendingBrep.length} onClick={addGroup}>
          Add averaged normal
        </button>
      </div>

      {(setup.manual.length > 0 || setup.brepGroups.length > 0) && (
        <ul className="dir-list">
          {setup.manual.map((v, i) => (
            <li key={`m${i}`}>
              <span>manual: [{v.map((c) => (+c).toFixed(2)).join(', ')}]</span>
              <button type="button" onClick={() => patch({ manual: setup.manual.filter((_, j) => j !== i) })}>×</button>
            </li>
          ))}
          {setup.brepGroups.map((g, i) => (
            <li key={`g${i}`}>
              <span>{g.length === 1 ? `BREP face ${g[0]}` : `avg of ${g.length} BREP faces`}</span>
              <button type="button" onClick={() => patch({ brepGroups: setup.brepGroups.filter((_, j) => j !== i) })}>×</button>
            </li>
          ))}
        </ul>
      )}

      {setup.suppressed.length > 0 && (
        <button type="button" onClick={() => patch({ suppressed: [] })}>
          Restore {setup.suppressed.length} hidden
        </button>
      )}
    </>
  );
}
