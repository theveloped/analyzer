import clsx from 'clsx';
import { Play, RotateCw } from 'lucide-react';
import { Button } from '../../catalyst/button';
import { getPlugin } from '../../registry';
import { useStore } from '../../state/store';
import { runAnalysisJob } from '../../viewer/jobs';
import { executionState, statusKindOf } from '../checks/status';
import { StatusBadge } from '../components/status';
import type { Lens } from '../lenses';
import { useActiveLens } from './hooks';
import { useBusy } from './run';
import { hintCls } from '../components/styles';
import type { ParamSpec } from '../../api/types';
import {
  Rail, RailDivider, RailHeader, RailSection, RailStats,
} from '../components/rail';
import {
  ParamsForm, type ParamWidget,
} from '../components/rail/ParamsForm';
import { lensActionFor } from './modeRails';


/** Run state + a Run button for a lens that paints one analysis's result.
 * Without this a lens with nothing cached can only tell the user to go find
 * the analysis in the generic Compute rail — which is where the hole-feature
 * lens dead-ended. */
function RunBacking({ lens }: { lens: Lens }) {
  const ref = lens.analysis!;
  const partId = useStore((s) => s.partId);
  const manifest = useStore((s) => s.manifest);
  const jobs = useStore((s) => s.jobs);
  const meshReady = useStore((s) => s.meshReady);
  const busy = useBusy();
  const state = executionState(manifest, jobs, partId, ref);
  const needsFine = !manifest?.mesh;

  function run() {
    if (!partId) return;
    runAnalysisJob(partId, ref.process, ref.analysis, {}).catch((err) =>
      useStore.getState().set({
        error: err instanceof Error ? err.message : String(err),
      }));
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="font-mono text-[11px]/4 text-zinc-500 dark:text-zinc-400">
          {ref.process}/{ref.analysis}
        </span>
        <StatusBadge status={statusKindOf({
          execution: state.execution,
          verdict: 'unknown',
          result: state.result,
          note: state.note,
        })}
        >
          {state.note || 'current'}
        </StatusBadge>
      </div>
      <Button
        outline
        className="w-full"
        disabled={!partId || !meshReady || busy}
        onClick={run}
      >
        {busy ? (
          <><RotateCw className="animate-spin" /> Running…</>
        ) : state.execution === 'current' ? (
          <><RotateCw /> Re-run</>
        ) : (
          <><Play /> Run</>
        )}
      </Button>
      {needsFine && (
        <p className={clsx('mt-2', hintCls)}>
          Runs on the fine mesh — this builds it first, which is the slow one.
        </p>
      )}
    </div>
  );
}

/** The generated settings section: the mode's declared params, bound to the
 * process's viewerParams bag (which is per-PROCESS, so this is a view onto a
 * subset of it rather than a private store). */
function LensParams({ processId, specs, overrides }: {
  processId: string;
  specs: ParamSpec[];
  overrides?: Record<string, ParamWidget>;
}) {
  const values = useStore((s) => s.viewerParams[processId]) ?? EMPTY;
  const setParam = useStore((s) => s.setViewerParam);
  return (
    <RailSection title="Settings">
      <ParamsForm
        specs={specs}
        values={values}
        overrides={overrides}
        target="viewer"
        onChange={(name, value) => setParam(processId, name, value)}
      />
    </RailSection>
  );
}

const EMPTY: Record<string, unknown> = {};

export function LensRail() {
  const lens = useActiveLens();
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const pick = useStore((s) => s.pick);
  if (!lens) return null;
  const Icon = lens.icon;
  const plugin = getPlugin(lens.processId);
  const mode = plugin?.modes.find((m) => m.id === lens.modeId);
  const specs = mode?.params ?? [];
  // one header control some lenses carry (the flat-pattern DXF export)
  const Action = lensActionFor(lens.processId, lens.modeId);

  return (
    <Rail>
      <RailHeader
        icon={Icon}
        title={lens.label}
        blurb={lens.blurb}
        actions={Action ? <Action /> : undefined}
      />

      {specs.length > 0 && (
        <LensParams processId={lens.processId} specs={specs}
          overrides={plugin?.paramWidgets} />
      )}

      {lens.analysis && <RunBacking lens={lens} />}

      <RailDivider />

      <RailStats text={stats} error={error} empty="Loading…" />

      <RailSection title="Inspect">
        <p className="whitespace-pre-wrap font-mono text-[11px]/4 text-zinc-500 dark:text-zinc-400">{pick}</p>
      </RailSection>
    </Rail>
  );
}
