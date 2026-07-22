// The Voxel render style self-materializes like field lenses do: picking it
// with no prep/voxels result triggers the analysis once (single-worker jobs
// API; the resolver auto-runs prep/mesh first if needed). Until it lands the
// viewport falls back to the solid look; the job's manifest refresh then
// feeds the shell through the controller.

import { flowVoxelResults } from '../../processes/injection/voxels';
import { useStore } from '../../state/store';
import { runAnalysisJob } from '../../viewer/jobs';
import { useV2 } from '../store';

const attempted = new Set<string>();
let initialized = false;

function maybeRun() {
  if (useV2.getState().viewport.style !== 'voxel') return;
  const { manifest, partId, jobs } = useStore.getState();
  if (!manifest || !partId) return;
  if (flowVoxelResults(manifest).length) return;
  if (jobs.some((j) => (j.status === 'queued' || j.status === 'running')
    && j.process === 'prep' && j.analysis === 'voxels')) return;
  if (attempted.has(partId)) return; // one shot — a failure surfaces, no loop
  attempted.add(partId);
  void runAnalysisJob(partId, 'prep', 'voxels', {});
}

/** Wire once per app (Viewer mount). */
export function initVoxelStyle() {
  if (initialized) return;
  initialized = true;
  useV2.subscribe((s, prev) => {
    if (s.viewport.style !== prev.viewport.style) maybeRun();
  });
  useStore.subscribe((s, prev) => {
    if (s.manifest !== (prev as typeof s).manifest) maybeRun();
  });
  maybeRun();
}
