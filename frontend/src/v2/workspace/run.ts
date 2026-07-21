import type { PlanCheck, PlanCheckStatus } from '../../api/types';
import { useStore } from '../../state/store';
import { runAnalysisJob } from '../../viewer/jobs';
import type { Analysis } from '../analyses';
import { defaultCompute } from '../analyses';
import { useV2 } from '../store';

/**
 * (Re)compute an analysis field. Submits a backend job with the engineer's
 * compute params (or the safe defaults) and lets the shared job watcher
 * refresh the manifest + repaint when it finishes.
 */
export function runAnalysis(a: Analysis): void {
  const partId = useStore.getState().partId;
  if (!partId) return;
  const compute = useV2.getState().compute[a.id] ?? defaultCompute(a);
  runAnalysisJob(partId, a.process, a.analysis, compute).catch((err) =>
    useStore.getState().set({
      error: err instanceof Error ? err.message : String(err),
    }),
  );
}

/** Run a PLAN check: submits the server-materialized params verbatim, so the
 * result lands exactly under the check's expected hash. */
export function runPlanCheck(check: PlanCheck, status: PlanCheckStatus | undefined): void {
  const partId = useStore.getState().partId;
  if (!partId || !status?.params) return;
  const [process, analysis] = check.analysis.split('/');
  runAnalysisJob(partId, process, analysis, status.params).catch((err) =>
    useStore.getState().set({
      error: err instanceof Error ? err.message : String(err),
    }),
  );
}

/** True while any job is queued/running for the active part. */
export function useBusy(): boolean {
  const partId = useStore((s) => s.partId);
  const jobs = useStore((s) => s.jobs);
  return jobs.some(
    (j) => j.part_id === partId && (j.status === 'queued' || j.status === 'running'),
  );
}
