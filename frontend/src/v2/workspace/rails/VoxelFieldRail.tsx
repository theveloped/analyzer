import { Grid3x3 } from 'lucide-react';
import { Select } from '../../../catalyst/select';
import { flowVoxelResults } from '../../../processes/injection/voxels';
import { useStore } from '../../../state/store';
import { runAnalysisJob } from '../../../viewer/jobs';
import {
  Rail, RailBool, RailField, RailHeader, RailRunButton, RailSection, RailStats,
} from '../../components/rail';
import { useBusy } from '../run';

/**
 * The voxel debug lens. Its own rail rather than a settings section because
 * the Compute button submits `prep/voxels` with an analysis param — `voxel` —
 * that is NOT the viewer param the field edits (`flowVoxel`). That mapping is
 * code, so no `ViewMode.params` declaration can carry it; the same reason PMI
 * and the candidate directions have their own rails.
 *
 * Everything above the button IS declarative, so it is the shared field
 * vocabulary rather than the `.row`/`.check` markup this replaces.
 */
export function VoxelFieldRail() {
  const manifest = useStore((s) => s.manifest);
  const partId = useStore((s) => s.partId);
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const meshReady = useStore((s) => s.meshReady);
  const params = useStore((s) => s.viewerParams.injection_molding) ?? EMPTY;
  const setParam = useStore((s) => s.setViewerParam);
  const busy = useBusy();
  const set = (name: string, value: unknown) =>
    setParam('injection_molding', name, value);

  const results = manifest ? flowVoxelResults(manifest) : [];

  function compute() {
    if (!partId) return;
    // the viewer edits `flowVoxel`; the analysis declares `voxel`
    const raw = parseFloat(String(params.flowVoxel ?? ''));
    runAnalysisJob(partId, 'prep', 'voxels', {
      voxel: isFinite(raw) ? raw : null,
    }).catch((err) => useStore.getState().set({
      error: err instanceof Error ? err.message : String(err),
    }));
  }

  return (
    <Rail>
      <RailHeader
        icon={Grid3x3}
        title="Voxel fields (debug)"
        blurb="The signed-distance grid the flow simulation runs on."
      />

      <RailSection title="Settings">
        <div className="flex flex-col gap-3">
          <RailField label="Voxel result">
            <Select
              value={String(params.flowResult ?? -1)}
              aria-label="Voxel result"
              onChange={(e) => set('flowResult', parseInt(e.target.value))}
            >
              {results.length > 0 && <option value={-1}>latest</option>}
              {results.map((r, i) => (
                <option key={r.hash} value={i}>
                  {`${r.stats.grid?.voxel?.toFixed(2)} mm · `
                    + `${r.stats.interior_voxels} voxels · ${r.hash}`}
                </option>
              ))}
              {!results.length && <option value={-1}>no results yet</option>}
            </Select>
          </RailField>

          <RailField label="Field">
            <Select
              value={String(params.voxelScalar ?? 'distance')}
              aria-label="Field"
              onChange={(e) => set('voxelScalar', e.target.value)}
            >
              <option value="distance">wall distance (SDF)</option>
              <option value="arrival">fill arrival (needs a fill result)</option>
              <option value="frozen">frozen state (needs a fill result)</option>
            </Select>
          </RailField>

          <RailBool
            label="Project onto the surface"
            checked={params.voxelSurface === true}
            onChange={(v) => set('voxelSurface', v)}
          />

          <RailField label="Voxel size" unit="mm"
            hint="Blank uses the resolution-derived default.">
            <input
              type="number"
              step="any"
              min={0}
              placeholder="auto"
              value={String(params.flowVoxel ?? '')}
              aria-label="Voxel size"
              onChange={(e) => set('flowVoxel', e.target.value)}
              className="w-full rounded-lg border border-zinc-950/10 bg-transparent px-2 py-1.5 text-sm/6 text-zinc-950 dark:border-white/10 dark:text-white"
            />
          </RailField>
        </div>
      </RailSection>

      <RailRunButton
        execution={results.length ? 'current' : 'not_run'}
        busy={busy}
        onRun={compute}
        disabled={!partId || !meshReady}
        runLabel="Compute voxels"
        rerunLabel="Recompute voxels"
        busyLabel="Computing…"
      />

      <RailStats text={stats} error={error} />
    </Rail>
  );
}

const EMPTY: Record<string, unknown> = {};
