import { useEffect } from 'react';
import { useStore } from '../../state/store';
import type { DirectionSetup } from './build';
import { EMPTY_SETUP } from './build';
import { loadSetup, saveSetup } from './state';

/**
 * The per-part direction setup, backed by the shared store (so the viewer
 * repaints live) and persisted to localStorage. Seeds itself when the part
 * changes. `patch` writes changed setup fields and saves; `params`/`setParam`
 * expose the raw bag for UI-only flags (pickMode, pendingBrep, selectedArrow…).
 */
export function useDirectionSetup() {
  const partId = useStore((s) => s.partId);
  const manifest = useStore((s) => s.manifest);
  const params = useStore((s) => s.viewerParams.directions) ?? {};
  const setParam = useStore((s) => s.setViewerParam);

  useEffect(() => {
    if (!partId) return;
    const state = useStore.getState();
    const cur = state.viewerParams.directions ?? {};
    if (cur.__part === partId) return; // already seeded for this part
    const setup = loadSetup(partId, manifest?.direction_sources ?? []);
    state.set({
      viewerParams: {
        ...state.viewerParams,
        directions: {
          ...setup, __part: partId,
          pickMode: false, pendingBrep: [], highlightBrep: [], selectedArrow: null,
        },
      },
    });
  }, [partId, manifest]);

  const setup: DirectionSetup = {
    count: params.count ?? EMPTY_SETUP.count,
    axes: params.axes ?? EMPTY_SETUP.axes,
    bboxAxes: params.bboxAxes ?? EMPTY_SETUP.bboxAxes,
    holeAxes: params.holeAxes ?? EMPTY_SETUP.holeAxes,
    manual: params.manual ?? EMPTY_SETUP.manual,
    brepGroups: params.brepGroups ?? EMPTY_SETUP.brepGroups,
    suppressed: params.suppressed ?? EMPTY_SETUP.suppressed,
  };

  const patch = (p: Partial<DirectionSetup>) => {
    const next = { ...setup, ...p };
    for (const key of Object.keys(p)) setParam('directions', key, (next as any)[key]);
    if (partId) saveSetup(partId, next);
  };

  return { setup, patch, params, setParam };
}
