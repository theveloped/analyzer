import type { Manifest } from '../../api/types';
import { useStore } from '../../state/store';
import type { Analysis } from '../analyses';
import { ANALYSIS_BY_ID, ANALYSES } from '../analyses';
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
