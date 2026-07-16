// "Split faces" section shared by the mold-assignment and CNC setups
// controls: the split-mode toggle, cut count, undo/clear buttons, a
// re-run button for stale results and Escape-to-cancel handling.

import { useEffect, useState } from 'react';
import { fetchSplits, type SplitsState } from '../api/client';
import { useStore } from '../state/store';
import {
  clearAllCuts, resubmitAssignment, undoLastCut, type SplitHost,
} from './splits';

const EMPTY: Record<string, any> = {};

export function SplitControls({ host }: { host: SplitHost }) {
  const partId = useStore((s) => s.partId);
  const manifest = useStore((s) => s.manifest);
  const manifestVersion = useStore((s) => s.manifestVersion);
  const params = useStore((s) => s.viewerParams[host.processId]) ?? EMPTY;
  const setParam = useStore((s) => s.setViewerParam);
  const set = (name: string, value: any) => setParam(host.processId, name, value);
  const [state, setState] = useState<SplitsState | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    if (partId) {
      fetchSplits(partId)
        .then((s) => { if (alive) setState(s); })
        .catch(() => { if (alive) setState(null); });
    } else setState(null);
    return () => { alive = false; };
  }, [partId, manifestVersion]);

  useEffect(() => {
    if (!params.splitMode) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      set('splitFace', null);
      set('splitStart', null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [params.splitMode]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = (action: () => Promise<void>) => {
    setBusy(true);
    action()
      .catch((err) => useStore.getState().set({
        error: err instanceof Error ? err.message : String(err),
      }))
      .finally(() => setBusy(false));
  };

  const result = manifest
    ? host.currentResult(manifest, params) : undefined;
  const cuts = state?.cuts.length ?? 0;

  return (
    <>
      <label className="check">
        <input
          type="checkbox" checked={params.splitMode === true}
          onChange={(e) => {
            set('splitMode', e.target.checked);
            set('splitFace', null);
            set('splitStart', null);
          }}
        />
        split faces (two boundary clicks)
      </label>

      {params.splitMode && (
        <div className="hint">
          click a face, then two of its marked wire points (corner or edge
          midpoint) — the cut runs between them · Esc cancels
        </div>
      )}

      {state?.stale && (
        <div className="hint">
          ⚠ cuts reference an older mesh — clear them to split again
        </div>
      )}

      {cuts > 0 && (
        <div className="row">
          <button disabled={busy} onClick={() => run(() => undoLastCut(host))}>
            {`undo last cut (${cuts})`}
          </button>
          <button disabled={busy} onClick={() => run(() => clearAllCuts(host))}>
            clear all cuts
          </button>
        </div>
      )}

      {result?.stale && (
        <button
          className="run" disabled={busy}
          onClick={() => run(() => resubmitAssignment(host))}
        >
          re-run assignment for current cuts
        </button>
      )}
    </>
  );
}
