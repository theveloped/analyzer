import type { Manifest } from '../../api/types';
import { useStore } from '../../state/store';
import type { Analysis } from '../analyses';
import { ANALYSIS_BY_ID, ANALYSES } from '../analyses';
import type { View } from '../views';
import { VIEW_BY_ID } from '../views';
import { useV2 } from '../store';

/** The active analysis is the shared store's modeId (falls back to thickness). */
export function useActiveAnalysis(): Analysis {
  const modeId = useStore((s) => s.modeId);
  return ANALYSIS_BY_ID[modeId] ?? ANALYSES[0];
}

/** Analyses visible in the shell — advanced ones only when advanced mode is on. */
export function useVisibleAnalyses(): Analysis[] {
  const advanced = useV2((s) => s.advanced);
  return ANALYSES.filter((a) => advanced || a.tier === 'primary');
}

/** Latest stored result for an analysis, if any (manifest lists oldest→newest). */
export function resultFor(manifest: Manifest | null, a: Analysis) {
  if (!manifest) return undefined;
  const list = manifest.results.filter(
    (r) => r.process === a.process && r.analysis === a.analysis,
  );
  return list[list.length - 1];
}

/** Switch the active analysis (drives the shared viewer's mode + process). */
export function selectAnalysis(a: Analysis) {
  useStore.getState().set({ processId: a.process, modeId: a.id });
}

/** The candidate-directions view is its own (cross-process) mode, not a check
 * in the ANALYSES catalog — it is active when the shared modeId is 'directions'. */
export function useDirectionsActive(): boolean {
  return useStore((s) => s.modeId) === 'directions';
}

/** Open the directions view (the shared controller paints the directionsPlugin). */
export function activateDirections() {
  useStore.getState().set({ processId: 'directions', modeId: 'directions' });
}

/** The active general view, if the shared modeId is one (else null). Views are
 * static geometry visualizations (BREP faces, STEP colors) — not checks. */
export function useActiveView(): View | null {
  const modeId = useStore((s) => s.modeId);
  return VIEW_BY_ID[modeId] ?? null;
}

/** Whether a general view is the active mode (used to suppress check highlights,
 * since `useActiveAnalysis` falls back to thickness for any non-check mode). */
export function useViewActive(): boolean {
  return useStore((s) => s.modeId in VIEW_BY_ID);
}

/** Switch to a general view (the shared controller paints the matching mode). */
export function selectView(v: View) {
  useStore.getState().set({ processId: v.process, modeId: v.id });
}
