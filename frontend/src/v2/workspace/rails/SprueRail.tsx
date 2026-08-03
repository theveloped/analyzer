import { Pin } from 'lucide-react';
import { Button } from '../../../catalyst/button';
import { Select } from '../../../catalyst/select';
import type { Proposal } from '../../../processes/injection/sprue';
import { useStore } from '../../../state/store';
import {
  Rail, RailBool, RailField, RailHeader, RailSection, RailStats,
} from '../../components/rail';
import { hintCls } from '../../components/styles';
import {
  Chip, ChipList, PickHint, pickResult, useInjectionParams,
} from './shared';

/**
 * Sprue proposals. Its own rail because selecting a proposal is a viewer
 * interaction — the markers on the part and the chips here are one selection,
 * and picking one can hand the gate to the fill-flow lens.
 */
export function SprueRail() {
  const manifest = useStore((s) => s.manifest);
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const { params, set } = useInjectionParams();

  const resultList = (manifest?.results ?? []).filter(
    (r) => r.process === 'injection_molding' && r.analysis === 'sprue_proposals'
      && r.stats.schema === 2);
  const result = pickResult(resultList, params.sprueResult);
  const proposals: Proposal[] = result?.stats.proposals ?? [];
  const chosen = params.proposal as number | null | undefined;
  const active = chosen != null ? proposals[chosen] : undefined;

  return (
    <Rail>
      <RailHeader
        icon={Pin}
        title="Sprue proposals"
        blurb="Ranked gate locations from the fill simulation."
      />

      <RailField label="Result (parameter set)">
        <Select
          value={String(params.sprueResult ?? -1)}
          aria-label="Result"
          onChange={(e) => {
            set('sprueResult', parseInt(e.target.value));
            set('proposal', null);
          }}
        >
          {resultList.length > 0 && <option value={-1}>latest</option>}
          {resultList.map((r, i) => (
            <option key={r.hash} value={i}>
              {`${r.stats.proposals?.length ?? 0} proposals · ${r.hash}`}
            </option>
          ))}
          {!resultList.length && <option value={-1}>no results yet</option>}
        </Select>
      </RailField>

      {proposals.length > 0 && (
        <RailSection title="Ranked proposals">
          <ChipList>
            {proposals.map((p) => (
              <Chip
                key={p.rank}
                selected={chosen === p.rank}
                onClick={() => set('proposal', chosen === p.rank ? null : p.rank)}
              >
                {`#${p.rank} · ${p.score.toFixed(2)}`}
                {p.gate_style !== 'unknown'
                  && ` · ${p.gate_style === 'edge' ? 'edge gate' : 'hot tip'}`}
                {p.side !== 'unknown' && ` · side ${p.side}`}
              </Chip>
            ))}
          </ChipList>
        </RailSection>
      )}

      {active && (
        <RailSection title={`Proposal #${active.rank}`}>
          <p className={hintCls}>
            {active.reasons.pros.map((r) => `+ ${r}`).join(' · ')}
            {active.reasons.cons.length > 0
              && ` · ${active.reasons.cons.map((r) => `− ${r}`).join(' · ')}`}
          </p>
          <div className="mt-2 flex gap-1.5">
            <Button
              onClick={() => {
                set('gate', active.point);
                useStore.getState().set({ modeId: 'skeleton' });
              }}
            >
              Open in fill flow
            </Button>
            <Button plain onClick={() => set('proposal', null)}>Clear</Button>
          </div>
        </RailSection>
      )}

      <RailSection title="Overlays">
        <div className="flex flex-col gap-2">
          <RailBool
            label="All candidates"
            hint="Score heatmap over every candidate, not just the ranked few."
            checked={params.showCandidates === true}
            onChange={(v) => set('showCandidates', v)}
          />
          <RailBool
            label="Weld indicator"
            checked={params.showWeld !== false}
            onChange={(v) => set('showWeld', v)}
          />
        </div>
      </RailSection>

      <PickHint>
        Click a marker on the part — or a proposal above — to inspect its fill.
      </PickHint>

      <RailStats text={stats} error={error} />
    </Rail>
  );
}
