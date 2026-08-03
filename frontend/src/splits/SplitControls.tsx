// "Split faces" section shared by the mold-assignment, CNC setups and turning
// rails: the split-mode toggle, cut count, undo/clear buttons, a re-run button
// for stale results and Escape-to-cancel handling.
//
// Built on the v2 rail vocabulary rather than the v1 `.check`/`.row`/`.run`
// classes, because it is embedded in three different surfaces and was the one
// piece forcing all three to stay inside the dark `.v1-controls` card.

import { useEffect, useState } from 'react';
import { fetchSplits, type SplitsState } from '../api/client';
import { Button } from '../catalyst/button';
import { useStore } from '../state/store';
import { RailAlert, RailBool, RailSection } from '../v2/components/rail';
import { hintCls } from '../v2/components/styles';
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
    <RailSection title="Face splits">
      <div className="flex flex-col gap-2">
        <RailBool
          label="Split faces"
          hint={params.splitMode
            ? 'Click a face, then two of its marked wire points (corner or '
              + 'edge midpoint) — the cut runs between them. Esc cancels.'
            : 'Two boundary clicks cut one face in two.'}
          checked={params.splitMode === true}
          onChange={(v) => {
            set('splitMode', v);
            set('splitFace', null);
            set('splitStart', null);
          }}
        />

        {state?.stale && (
          <RailAlert>
            Cuts reference an older mesh — clear them to split again.
          </RailAlert>
        )}

        {cuts > 0 && (
          <>
            <RailBool
              label="Show cut lines"
              checked={params.showCuts !== false}
              onChange={(v) => set('showCuts', v)}
            />
            <div className="flex gap-1.5">
              <Button outline disabled={busy}
                onClick={() => run(() => undoLastCut(host))}>
                {`Undo last (${cuts})`}
              </Button>
              <Button plain disabled={busy}
                onClick={() => run(() => clearAllCuts(host))}>
                Clear all
              </Button>
            </div>
          </>
        )}

        {result?.stale && (
          <div>
            <Button disabled={busy} className="w-full"
              onClick={() => run(() => resubmitAssignment(host))}>
              Re-run assignment for current cuts
            </Button>
            <p className={`mt-1 ${hintCls}`}>
              The stored assignment predates these cuts.
            </p>
          </div>
        )}
      </div>
    </RailSection>
  );
}
