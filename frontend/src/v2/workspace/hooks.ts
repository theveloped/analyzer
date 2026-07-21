import { useStore } from '../../state/store';
import type { Analysis } from '../analyses';
import { ANALYSIS_BY_ID, ANALYSES } from '../analyses';
import { checkState, type CheckState } from '../checks/status';
import type { Lens } from '../lenses';
import { lensFor } from '../lenses';
import { useV2 } from '../store';

/** The active analysis is the shared store's modeId (falls back to thickness). */
export function useActiveAnalysis(): Analysis {
  const modeId = useStore((s) => s.modeId);
  return ANALYSIS_BY_ID[modeId] ?? ANALYSES[0];
}

/** Whether the active mode is one of the runnable checks (vs a plain lens). */
export function useCheckActive(): boolean {
  return useStore((s) => s.modeId in ANALYSIS_BY_ID);
}

/** Analyses visible in the shell — advanced ones only when advanced mode is on. */
export function useVisibleAnalyses(): Analysis[] {
  const advanced = useV2((s) => s.advanced);
  return ANALYSES.filter((a) => advanced || a.tier === 'primary');
}

/** Switch the active analysis (drives the shared viewer's mode + process). */
export function selectAnalysis(a: Analysis) {
  useStore.getState().set({ processId: a.process, modeId: a.id });
}

/** Execution + verdict state of a check, from the live store (manifest,
 * jobs, and the engineer's current threshold — provisional until Phase 1
 * pins policies on plan checks). */
export function useCheckState(a: Analysis): CheckState {
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  const partId = useStore((s) => s.partId);
  const params = useStore((s) => s.viewerParams[a.process]);
  const threshold = Number((params ?? {})[a.thresholdParam] ?? a.thresholdDefault);
  return checkState(manifest, jobs, partId, a, threshold);
}

/** The candidate-directions view is its own (cross-process) mode with a
 * dedicated toolbar button — active when the shared modeId is 'directions'. */
export function useDirectionsActive(): boolean {
  return useStore((s) => s.modeId) === 'directions';
}

/** Open the directions view (the shared controller paints the directionsPlugin). */
export function activateDirections() {
  useStore.getState().set({ processId: 'directions', modeId: 'directions' });
}

/** The active inspection lens, if the shared process/mode is registered as
 * one (checks and directions also resolve — rails decide precedence). */
export function useActiveLens(): Lens | null {
  const processId = useStore((s) => s.processId);
  const modeId = useStore((s) => s.modeId);
  return lensFor(processId, modeId);
}

/** Activate an inspection lens (drives the shared viewer's mode + process). */
export function selectLens(l: Lens) {
  useStore.getState().set({ processId: l.processId, modeId: l.modeId });
}
