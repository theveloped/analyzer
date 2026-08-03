import { Layers } from 'lucide-react';
import { Button } from '../../../catalyst/button';
import { Select } from '../../../catalyst/select';
import {
  loadAssignment, moldSplitHost, resultsFor,
} from '../../../processes/injection';
import { optimizeParting } from '../../../processes/parting';
import { SplitControls } from '../../../splits/SplitControls';
import { useStore } from '../../../state/store';
import { runCtxAction } from '../../../viewer/controller';
import {
  Rail, RailBool, RailField, RailHeader, RailSection, RailStats,
} from '../../components/rail';
import { hintCls } from '../../components/styles';
import { PickHint, pickResult, useInjectionParams } from './shared';

/**
 * Mold orientation assignment. Its own rail for three separate reasons, any
 * one of which would be enough: clicking a face cycles it between its valid
 * sides, the parting-line optimizer is an action over the loaded assignment
 * rather than a param, and it embeds the face-split editor.
 */
export function AssignmentRail() {
  const manifest = useStore((s) => s.manifest);
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const { params, set } = useInjectionParams();

  const results = manifest ? resultsFor(manifest, 'mold_orientation') : [];
  const result = pickResult(results, params.result as number | null);
  const options: any[] = result?.stats.options ?? [];
  const fieldOptions = options.slice(0, 3);
  const hasBrep = !!manifest?.fields.some((f) => f.id === 'brep_edges');

  return (
    <Rail>
      <RailHeader
        icon={Layers}
        title="Mold orientation"
        blurb="Which side of the tool each face is formed by."
      />

      <RailField label="Result (parameter set)">
        <Select
          value={String(params.result ?? -1)}
          aria-label="Result"
          onChange={(e) => set('result', parseInt(e.target.value))}
        >
          {results.length > 0 && <option value={-1}>latest</option>}
          {results.map((r, i) => (
            <option key={r.hash} value={i}>
              {`max slides ${r.params.max_slides ?? '?'} · ${r.hash}`}
            </option>
          ))}
          {!results.length && <option value={-1}>no results yet</option>}
        </Select>
      </RailField>

      <RailField label="Orientation option">
        <Select
          value={String(params.option ?? 0)}
          aria-label="Orientation option"
          onChange={(e) => set('option', parseInt(e.target.value))}
        >
          {fieldOptions.map((o, i) => (
            <option key={i} value={i}>
              {`±d${o.pair[0]} · ${o.slides.length} slide(s) · `
                + `${o.feasible ? 'feasible' : 'infeasible'}`}
            </option>
          ))}
          {!fieldOptions.length && <option value={0}>—</option>}
        </Select>
      </RailField>

      <RailSection title="Overlays">
        <div className="flex flex-col gap-2">
          <RailBool label="Parting lines" checked={params.showLines !== false}
            onChange={(v) => set('showLines', v)} />
          <RailBool label="Direction arrows" checked={params.showArrows !== false}
            onChange={(v) => set('showArrows', v)} />
        </div>
      </RailSection>

      <PickHint>
        Click a face to cycle it between its valid sides and slides — faded
        stripes mark the other valid choices.
      </PickHint>

      <Button
        outline
        className="w-full"
        disabled={!hasBrep || !results.length}
        onClick={() => void runCtxAction(async (ctx) => {
          const data = await loadAssignment(ctx);
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

      <SplitControls host={moldSplitHost} />

      {options.length > 0 && (
        <RailSection title="Ranked options">
          <p className={hintCls}>
            {options.map((o, i) =>
              `#${i} ±d${o.pair[0]} ${o.feasible ? '✓' : '✗'} `
              + `${(o.coverage * 100).toFixed(0)}%`).join(' · ')}
          </p>
        </RailSection>
      )}

      <RailStats text={stats} error={error} />
    </Rail>
  );
}
