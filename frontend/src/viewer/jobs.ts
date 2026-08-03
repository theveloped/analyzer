// Job submission + poll loop shared by the Compute rail and plugin
// controls (e.g. the flow-fill "Compute fill" button). The watched set is
// module-level so remounts and multiple callers never double-poll a job.

import {
  fetchJob, reprocessPart as reprocessPartApi, submitJob, uploadPart,
} from '../api/client';
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

/** Upload a STEP/STL file, select the new part and — for STEP — watch the
 * first-load bundle so the coarse preview appears on its own. Without the
 * watch nothing ever polls: `selectPart` runs while the bundle is still
 * queued, so the manifest has no coarse mesh yet and only a manual reload
 * would surface it. Lives here rather than in the controller because
 * `watchJob` calls back into the controller. */
export async function uploadAndSelect(file: File): Promise<void> {
  const { part, job } = await uploadPart(file);
  await refreshParts();
  await selectPart(part.id);
  if (job) {
    useStore.getState().set({ jobs: [job, ...useStore.getState().jobs] });
    void watchJob(job);
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

/** prep/bundle keeps going when one of its stages fails, so that a broken
 * AAG still leaves a usable preview — which means a failed stage arrives in
 * a job whose status is `done`. Surface it, or a part that never renders
 * looks like a success. */
function reportBundleErrors(job: Job): void {
  const bundle = job.result?.stats?.bundle as
    Record<string, { error?: string }> | undefined;
  if (!bundle) return;
  const failed = Object.entries(bundle)
    .filter(([, stage]) => typeof stage?.error === 'string')
    .map(([target, stage]) => `${target} — ${stage.error}`);
  if (failed.length) {
    useStore.getState().set({ error: `first-load: ${failed.join(' · ')}` });
  }
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
      reportBundleErrors(current);
      await refreshParts();
      await refreshManifest();
      await onDone?.();
      schedulePaint(true);
    } else if (current.status === 'cancelled') {
      // prep artifacts finished before the cancel are valid — surface them
      await refreshManifest();
      schedulePaint(true);
    }
  } finally {
    watched.delete(job.id);
  }
}
