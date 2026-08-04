import { ArrowUpFromLine } from 'lucide-react';
import { Button } from '../../../catalyst/button';
import { Input } from '../../../catalyst/input';
import { Select } from '../../../catalyst/select';
import type { Pin as EjectorPin } from '../../../processes/injection/ejector';
import { useStore } from '../../../state/store';
import {
  Rail, RailBool, RailField, RailHeader, RailSection, RailStats,
} from '../../components/rail';
import { Chip, ChipList, PickHint, useInjectionParams } from './shared';

const DIAMETERS = [2, 3, 4, 6, 8];

/**
 * Ejector pins. Its own rail because a pin is PLACED by clicking the part and
 * removed by clicking it again — the chips here and the markers in the viewer
 * are one list, which no settings form can express.
 */
export function EjectorRail() {
  const manifest = useStore((s) => s.manifest);
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const { params, set } = useInjectionParams();

  const resultList = (manifest?.results ?? []).filter(
    (r) => r.process === 'injection_molding'
      && r.analysis === 'ejection_sticking' && r.stats.schema === 2);
  const pins: EjectorPin[] = (params.pins as EjectorPin[]) ?? [];
  const sim = (params.ejSim as any) ?? null;

  return (
    <Rail>
      <RailHeader
        icon={ArrowUpFromLine}
        title="Ejector pins"
        blurb="Place pins and check the force each one carries."
      />

      <RailField label="Result (parameter set)">
        <Select
          value={String(params.stickResult ?? -1)}
          aria-label="Result"
          onChange={(e) => set('stickResult', parseInt(e.target.value))}
        >
          {resultList.length > 0 && <option value={-1}>latest</option>}
          {resultList.map((r, i) => (
            <option key={r.hash} value={i}>
              {`${(r.stats.totals?.sticking_force_n ?? 0).toFixed(0)} N sticking · ${r.hash}`}
            </option>
          ))}
          {!resultList.length && <option value={-1}>no results yet</option>}
        </Select>
      </RailField>

      <RailField label="Pin diameter" unit="mm">
        <Select
          value={String(params.pinDiameter ?? 3)}
          aria-label="Pin diameter"
          onChange={(e) => set('pinDiameter', parseFloat(e.target.value))}
        >
          {DIAMETERS.map((d) => <option key={d} value={d}>{`Ø${d}`}</option>)}
        </Select>
      </RailField>

      <RailBool
        label="Draft-angle view"
        checked={params.ejShowDraft === true}
        onChange={(v) => set('ejShowDraft', v)}
      />

      <RailSection title="Material">
        <div className="flex flex-col gap-3">
          <RailField label="E modulus" unit="MPa">
            <Input type="number" step="any" aria-label="E modulus"
              value={String(params.ejE ?? 2000)}
              onChange={(e) => set('ejE', e.target.value)} />
          </RailField>
          <RailField label="Allowable pin pressure" unit="MPa">
            <Input type="number" step="any" aria-label="Allowable pin pressure"
              value={String(params.ejAllow ?? 80)}
              onChange={(e) => set('ejAllow', e.target.value)} />
          </RailField>
        </div>
      </RailSection>

      {pins.length > 0 && (
        <RailSection
          title={`Placed pins (${pins.length})`}
          action={(
            <Button plain onClick={() => set('pins', [])}>Clear</Button>
          )}
        >
          <ChipList>
            {pins.map((p, i) => (
              <Chip
                key={i}
                tone={sim?.pins?.[i]?.over_limit ? 'bad' : 'neutral'}
                title="Remove this pin"
                onClick={() => set('pins', pins.filter((_, j) => j !== i))}
              >
                {`#${i} · Ø${p.diameter}`}
                {sim?.pins?.[i] && (
                  ` · ${sim.pins[i].force_n.toFixed(1)} N`
                  + ` · ${sim.pins[i].pressure_mpa.toFixed(1)} MPa`
                  + ` (${(100 * sim.pins[i].utilization).toFixed(0)}%)`
                )}
                {' ✕'}
              </Chip>
            ))}
          </ChipList>
        </RailSection>
      )}

      <PickHint>
        Click the part to add a pin at the chosen diameter; click a pin —
        marker or chip — to remove it.
      </PickHint>

      <RailStats text={stats} error={error} />
    </Rail>
  );
}
