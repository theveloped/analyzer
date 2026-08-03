import { Droplets } from 'lucide-react';
import { Button } from '../../../catalyst/button';
import { Input } from '../../../catalyst/input';
import { Select } from '../../../catalyst/select';
import { flowFillResults } from '../../../processes/injection/voxels';
import { useStore } from '../../../state/store';
import { runAnalysisJob } from '../../../viewer/jobs';
import {
  Rail, RailField, RailHeader, RailRunButton, RailSection, RailStats,
} from '../../components/rail';
import { useBusy } from '../run';
import { PickHint, useInjectionParams } from './shared';

const num = (v: unknown, fallback: number) => {
  const n = parseFloat(String(v));
  return isFinite(n) ? n : fallback;
};

/**
 * The fill simulation. Two reasons this cannot be a declaration: the gate is
 * placed by clicking the part, and the job's params are a MAPPING of the
 * viewer's — `flowSkinCoef` becomes `skin_coef`, `flowDelta0` becomes
 * `delta0`, `flowVoxel` becomes `voxel`. That translation is code.
 */
export function FlowFillRail() {
  const manifest = useStore((s) => s.manifest);
  const partId = useStore((s) => s.partId);
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const busy = useBusy();
  const { params, set } = useInjectionParams();
  const results = manifest ? flowFillResults(manifest) : [];
  const gate = params.gate as number[] | null | undefined;

  function compute() {
    if (!partId) return;
    const voxel = Number.isFinite(parseFloat(String(params.flowVoxel)))
      ? parseFloat(String(params.flowVoxel)) : null;
    runAnalysisJob(partId, 'injection_molding', 'flow_fill', {
      voxel,
      gate,
      delta0: num(params.flowDelta0, 0),
      skin_coef: num(params.flowSkinCoef, 0.12),
      fill_time: num(params.flowFillTime, 2),
      iterations: Math.max(1, Math.round(num(params.flowIterations, 3))),
      neighborhood: String(params.flowNeighborhood ?? '26'),
    })
      .then(() => set('fillResult', -1))
      .catch((err) => useStore.getState().set({
        error: err instanceof Error ? err.message : String(err),
      }));
  }

  const number = (name: string, label: string, unit: string, fallback: string) => (
    <RailField label={label} unit={unit}>
      <Input
        type="number" step="any" aria-label={label}
        placeholder={fallback === '' ? 'auto' : undefined}
        value={String(params[name] ?? fallback)}
        onChange={(e) => set(name, e.target.value)}
      />
    </RailField>
  );

  return (
    <Rail>
      <RailHeader
        icon={Droplets}
        title="Flow fill"
        blurb="Fill arrival and frozen skin from the voxel grid."
      />

      <RailField label="Fill result">
        <Select
          value={String(params.fillResult ?? -1)}
          aria-label="Fill result"
          onChange={(e) => set('fillResult', parseInt(e.target.value))}
        >
          {results.length > 0 && <option value={-1}>latest</option>}
          {results.map((r, i) => (
            <option key={r.hash} value={i}>
              {`gate (${(r.stats.gate?.point ?? [])
                .map((c: number) => c.toFixed(0)).join(', ')})`
                + ` · skin ${r.stats.fill?.skin_coef} · ${r.hash}`}
            </option>
          ))}
          {!results.length && <option value={-1}>no results yet</option>}
        </Select>
      </RailField>

      <RailSection title="Simulation">
        <div className="flex flex-col gap-3">
          {number('flowVoxel', 'Voxel size', 'mm', '')}
          {number('flowFillTime', 'Fill time', 's', '2')}
          {number('flowSkinCoef', 'Skin growth', 'mm/√s', '0.12')}
          {number('flowDelta0', 'Initial skin', 'mm', '0')}
          {number('flowIterations', 'Skin passes', '', '3')}
          <RailField label="Neighborhood">
            <Select
              value={String(params.flowNeighborhood ?? '26')}
              aria-label="Neighborhood"
              onChange={(e) => set('flowNeighborhood', e.target.value)}
            >
              <option value="26">26 (isotropic)</option>
              <option value="6">6 (fast)</option>
            </Select>
          </RailField>
        </div>
      </RailSection>

      <div>
        <RailRunButton
          execution={results.length ? 'current' : 'not_run'}
          busy={busy}
          onRun={compute}
          disabled={!gate || !partId}
          runLabel="Compute fill"
          rerunLabel="Recompute fill"
          busyLabel="Computing…"
        />
        {gate ? (
          <Button plain className="mt-2" onClick={() => set('gate', null)}>
            Clear gate
          </Button>
        ) : (
          <PickHint>
            Click the part to place the gate — the simulation needs one before
            it can run.
          </PickHint>
        )}
      </div>

      <RailStats text={stats} error={error} />
    </Rail>
  );
}
