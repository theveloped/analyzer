import { Axis3d } from 'lucide-react';
import { Button } from '../../../catalyst/button';
import { Select } from '../../../catalyst/select';
import {
  cncSplitHost, loadSetups, optionLabel, resultLabel, setupsResults,
} from '../../../processes/cnc/setups';
import { optimizeParting } from '../../../processes/parting';
import { SplitControls } from '../../../splits/SplitControls';
import { useStore } from '../../../state/store';
import { runCtxAction } from '../../../viewer/controller';
import {
  Rail, RailBool, RailField, RailHeader, RailSection, RailStats,
} from '../../components/rail';
import { hintCls } from '../../components/styles';
import { PickHint } from './shared';

const EMPTY: Record<string, unknown> = {};

/**
 * Setup combinations. Its own rail for the same reasons the mold assignment
 * has one — clicking a face cycles it between the setups that can machine it,
 * the parting-line optimizer is an action rather than a param, and it embeds
 * the face-split editor.
 *
 * The two are near-twins by nature: both paint an assignment over the part and
 * let you override it per face. They deliberately stay separate components
 * rather than one generic "assignment rail", because their vocabularies differ
 * (setup plans vs orientation options) and merging them would mean a prop for
 * every word on screen.
 */
export function SetupsRail() {
  const manifest = useStore((s) => s.manifest);
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const params = useStore((s) => s.viewerParams.cnc) ?? EMPTY;
  const setParam = useStore((s) => s.setViewerParam);
  const set = (name: string, value: unknown) => setParam('cnc', name, value);

  const results = manifest ? setupsResults(manifest) : [];
  const result = results[(params.setupsResult as number) ?? 0]
    ?? results[results.length - 1];
  const options: any[] = result?.stats.options ?? [];
  const fieldOptions: number[] = result?.stats.field_options ?? [];
  const hasBrep = !!manifest?.fields.some((f) => f.id === 'brep_edges');

  return (
    <Rail>
      <RailHeader
        icon={Axis3d}
        title="Setup assignment"
        blurb="Which setup machines each face, for the ranked plan below."
      />

      <RailField label="Result (parameter set)">
        <Select
          value={String(params.setupsResult ?? 0)}
          aria-label="Result"
          onChange={(e) => {
            set('setupsResult', parseInt(e.target.value));
            set('setupsOption', 0);
          }}
        >
          {results.map((r, i) => (
            <option key={r.hash} value={i}>{resultLabel(r)}</option>
          ))}
          {!results.length && <option value={0}>no results yet</option>}
        </Select>
      </RailField>

      <RailField label="Setup plan">
        <Select
          value={String(params.setupsOption ?? 0)}
          aria-label="Setup plan"
          onChange={(e) => set('setupsOption', parseInt(e.target.value))}
        >
          {fieldOptions.map((index, k) => (
            <option key={k} value={k}>{optionLabel(options[index])}</option>
          ))}
          {!fieldOptions.length && <option value={0}>—</option>}
        </Select>
      </RailField>

      <RailSection title="Overlays">
        <div className="flex flex-col gap-2">
          <RailBool label="Setup boundaries" checked={params.showLines !== false}
            onChange={(v) => set('showLines', v)} />
          <RailBool label="Direction arrows" checked={params.showArrows !== false}
            onChange={(v) => set('showArrows', v)} />
        </div>
      </RailSection>

      <PickHint>
        Click a face to cycle it between the setups that can machine it —
        faded stripes mark the other valid setups.
      </PickHint>

      <Button
        outline
        className="w-full"
        disabled={!hasBrep || !results.length}
        onClick={() => void runCtxAction(async (ctx) => {
          const data = await loadSetups(ctx);
          const { summary, changed } = await optimizeParting(ctx, {
            valid: data.valid, defaults: data.defaults, current: data.current,
            option: data.option, overridesKey: data.overridesKey,
            overridesUrl: data.result.overrides_url,
          });
          useStore.getState().set({ pick: summary });
          return changed;
        })}
      >
        Optimize parting lines
      </Button>

      <SplitControls host={cncSplitHost} />

      {options.length > 0 && (
        <RailSection title="Ranked plans">
          <p className={hintCls}>
            {options.slice(0, 6).map((o, i) =>
              `#${i} ${o.machine}×${o.setups.length} ${o.feasible ? '✓' : '✗'}`)
              .join(' · ')}
          </p>
        </RailSection>
      )}

      <RailStats text={stats} error={error} />
    </Rail>
  );
}
