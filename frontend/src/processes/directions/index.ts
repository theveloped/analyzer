// The cross-process "Directions" view: visualize every candidate approach
// direction with its provenance, and add/remove directions (manual axis,
// picked-face averaged normal, hole/PCA axes). Directions live as
// prep/directions params — adding one re-runs that analysis, which is why the
// Controls stage edits and apply them with an explicit "Recompute" button.

import type { ProcessPlugin, ViewCtx } from '../../registry/types';
import { useStore } from '../../state/store';
import { currentBrepIds } from './build';
import { DirectionsControls } from './Controls';
import { directionsMode } from './modes';

// Clicking the mesh while picking selects the whole BREP face the facet
// belongs to (not the raw mesh triangle), toggling it in the pending set.
function onPick(face: number, _point: [number, number, number], _ctx: ViewCtx): boolean {
  const { viewerParams, setViewerParam } = useStore.getState();
  const params = viewerParams.directions ?? {};
  if (!params.pickMode || !currentBrepIds) return false;
  const brep = currentBrepIds[face];
  const pending: number[] = params.pendingBrep ?? [];
  const next = pending.includes(brep)
    ? pending.filter((b) => b !== brep)
    : [...pending, brep];
  setViewerParam('directions', 'pendingBrep', next);
  return true; // consumed — store change repaints and re-highlights
}

export const directionsPlugin: ProcessPlugin = {
  processId: 'directions',
  label: 'Directions',
  modes: [directionsMode],
  defaults: () => ({ pickMode: false, pendingBrep: [] }),
  Controls: DirectionsControls,
  onPick,
};
