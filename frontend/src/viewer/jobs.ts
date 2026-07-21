// Job submission + poll loop shared by the Compute panel and plugin
// controls (e.g. the flow-fill "Compute fill" button). The watched set is
// module-level so remounts and multiple callers never double-poll a job.

import { fetchJob, reprocessPart as reprocessPartApi, submitJob } from '../api/client';
import type { Job } from '../api/types';
import { useStore } from '../state/store';
import {
  refreshManifest, refreshParts, schedulePaint, selectPart,
} from './controller';

const watched = new Set<number>();

/** Force a from-scratch rebuild of a part from its original source, bypassing
 * the content-addressed cache (for algorithm changes the resolver can't see).
 * Wipes the cached artifacts server-side, reloads the (now bare) part, and —
 * for STEP — watches the first-load bundle so the preview reappears, then
 * reloads the mesh in case the rebuild re-cut it. */
export async function reprocessPart(partId: string): Promise<void> {
  const { job } = await reprocessPartApi(partId);
  await selectPart(partId); // reflect the wiped state immediately
  if (job) {
    useStore.getState().set({ jobs: [job, ...useStore.getState().jobs] });
    void watchJob(job, () => selectPart(partId));
  }
}

/** Submit an analysis job, register it in the store and start watching.
 * `onDone` runs after a successful job's manifest refresh (e.g. carrying
 * assignment overrides forward to the recomputed result). */
export async function runAnalysisJob(
  partId: string, processId: string, analysisId: string,
  params: Record<string, any>, onDone?: () => void | Promise<void>,
): Promise<Job> {
  const job = await submitJob(partId, processId, analysisId, params);
  useStore.getState().set({ jobs: [job, ...useStore.getState().jobs] });
  void watchJob(job, onDone);
  return job;
}

/** Poll a queued/running job until it settles; refresh the manifest and
 * repaint on success so new fields appear in the view selectors. */
export async function watchJob(
  job: Job, onDone?: () => void | Promise<void>,
): Promise<void> {
  if (watched.has(job.id)) return;
  watched.add(job.id);
  try {
    let current = job;
    let misses = 0;
    while (current.status === 'queued' || current.status === 'running') {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      try {
        current = await fetchJob(job.id);
        misses = 0;
      } catch {
        // transient poll failure (sleep, hiccup) must not orphan a job
        // that is still running server-side; a restarted server forgets
        // its jobs, so persistent failures mean the job is gone
        if (++misses >= 30) break;
        continue;
      }
      useStore.getState().set({
        jobs: useStore.getState().jobs.map((j) => (j.id === current.id ? current : j)),
      });
    }
    if (current.status === 'done') {
      await refreshParts();
      await refreshManifest();
      await onDone?.();
      schedulePaint(true);
    }
  } finally {
    watched.delete(job.id);
  }
}
