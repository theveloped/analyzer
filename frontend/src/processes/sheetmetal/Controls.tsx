// Sheet metal viewer controls: DXF download of the current flat pattern,
// and the bend-sequence playhead (scrubber + play/pause + speed).

import { useEffect, useReducer } from 'react';
import { useStore } from '../../state/store';
import { SHEET_SCHEMA } from './index';
import { playhead, seekPlayhead, stepOfPos } from './bendsequence';

function BendSequenceControls() {
  const [, bump] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    const listener = () => bump();
    playhead.listeners.add(listener);
    return () => { playhead.listeners.delete(listener); };
  }, []);

  if (!playhead.steps) return null;
  const step = stepOfPos(playhead.pos, playhead.steps);
  return (
    <div className="control-group">
      <label style={{ fontSize: 12 }}>
        {`bend sequence — step ${step + 1}/${playhead.steps}`}
      </label>
      <input
        type="range"
        min={0}
        max={playhead.steps * 1000}
        value={Math.round(playhead.pos * 1000)}
        onChange={(e) => {
          playhead.playing = false;
          seekPlayhead(Number(e.target.value) / 1000);
        }}
        style={{ width: '100%' }}
      />
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <button
          type="button"
          onClick={() => {
            if (!playhead.playing && playhead.pos >= playhead.steps) {
              seekPlayhead(0);
            }
            playhead.playing = !playhead.playing;
            playhead.notify();
          }}
        >
          {playhead.playing ? '⏸ pause' : '▶ play'}
        </button>
        <select
          value={playhead.speed}
          onChange={(e) => {
            playhead.speed = Number(e.target.value);
            playhead.notify();
          }}
        >
          <option value={0.5}>0.5×</option>
          <option value={1}>1×</option>
          <option value={2}>2×</option>
          <option value={4}>4×</option>
        </select>
      </div>
    </div>
  );
}

export function SheetMetalControls() {
  const manifest = useStore((s) => s.manifest);
  const modeId = useStore((s) => s.modeId);
  if (!manifest) return null;

  const results = manifest.results.filter((r) => r.process === 'sheet_metal'
    && r.analysis === 'flat_pattern' && !r.stale
    && r.params.schema === SHEET_SCHEMA);
  const result = results.length ? results[results.length - 1] : null;
  const url = result
    ? `/api/parts/${manifest.part.id}/results/sheet_metal/flat_pattern/${result.hash}/export/dxf`
    : null;

  return (
    <>
      {modeId === 'bend_sequence' && <BendSequenceControls />}
      {url && (
        <div className="control-group">
          <a href={url} download style={{ fontSize: 12 }}>
            ⤓ Download flat pattern DXF
          </a>
        </div>
      )}
    </>
  );
}
