import { Disclosure, DisclosureButton, DisclosurePanel } from '@headlessui/react';
import clsx from 'clsx';
import { ChevronDown, Play, RotateCw, Settings2 } from 'lucide-react';
import { AnalysisPanel } from '../../components/AnalysisPanel';
import { Button } from '../../catalyst/button';
import { getPlugin } from '../../registry';
import { useStore } from '../../state/store';
import { runAnalysisJob } from '../../viewer/jobs';
import { executionState, statusKindOf } from '../checks/status';
import { StatusBadge } from '../components/status';
import type { Lens } from '../lenses';
import { useActiveLens } from './hooks';
import { useBusy } from './run';
import './v1-controls.css';
import { hintCls } from '../components/styles';


/** Run state + a Run button for a lens that paints one analysis's result.
 * Without this a lens with nothing cached can only tell the user to go find
 * the analysis in the generic Compute panel — which is where the hole-feature
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

/**
 * The right rail for an active inspection lens: label/blurb, the shared
 * paint stats, and — when the hosting plugin ships a Controls panel — a
 * Configure section rendering that panel verbatim under the `.v1-controls`
 * scope (the visual seam is accepted for now; see docs/PLAN-ARCHITECTURE.md).
 */
export function LensRail() {
  const lens = useActiveLens();
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const pick = useStore((s) => s.pick);
  if (!lens) return null;
  const Icon = lens.icon;
  const Controls = lens.hasControls ? getPlugin(lens.processId)?.Controls : undefined;

  return (
    <div className="flex min-h-full flex-col gap-4 p-4">
      <div>
        <div className="flex items-center gap-2">
          <Icon className="size-4 text-blue-600 dark:text-blue-400" />
          <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">{lens.label}</h2>
        </div>
        {lens.blurb && <p className={clsx('mt-1', hintCls)}>{lens.blurb}</p>}
      </div>

      {lens.analysis && <RunBacking lens={lens} />}

      {Controls && (
        <Disclosure defaultOpen>
          {({ open }) => (
            <div>
              <DisclosureButton className="flex w-full items-center justify-between rounded-lg px-1 py-1 text-xs/5 font-medium text-zinc-500 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white">
                <span className="flex items-center gap-1.5">
                  <Settings2 className="size-3.5" /> Configure
                </span>
                <ChevronDown className={clsx('size-3.5 transition-transform', open && 'rotate-180')} />
              </DisclosureButton>
              <DisclosurePanel className="mt-2">
                <div className="v1-controls">
                  <Controls />
                </div>
              </DisclosurePanel>
            </div>
          )}
        </Disclosure>
      )}

      {/* every analysis stays runnable while lenses grow their own flows:
          the v1 compute panel (catalog picker + auto-generated param form)
          hosted verbatim — enough to materialize any lens's prerequisites */}
      <Disclosure defaultOpen={!Controls}>
        {({ open }) => (
          <div>
            <DisclosureButton className="flex w-full items-center justify-between rounded-lg px-1 py-1 text-xs/5 font-medium text-zinc-500 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white">
              <span className="flex items-center gap-1.5">
                <Play className="size-3.5" /> Compute
              </span>
              <ChevronDown className={clsx('size-3.5 transition-transform', open && 'rotate-180')} />
            </DisclosureButton>
            <DisclosurePanel className="mt-2">
              <div className="v1-controls">
                <AnalysisPanel />
              </div>
            </DisclosurePanel>
          </div>
        )}
      </Disclosure>

      <div className="h-px bg-zinc-950/10 dark:bg-white/10" />

      <div>
        <div className="mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">In view</div>
        {error ? (
          <p className="whitespace-pre-wrap text-xs/5 text-red-600 dark:text-red-500">⚠ {error}</p>
        ) : stats ? (
          <p className="whitespace-pre-wrap text-xs/5 text-zinc-500 dark:text-zinc-400">{stats}</p>
        ) : (
          <p className={hintCls}>Loading…</p>
        )}
      </div>

      <div>
        <div className="mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">Inspect</div>
        <p className="whitespace-pre-wrap font-mono text-[11px]/4 text-zinc-500 dark:text-zinc-400">{pick}</p>
      </div>
    </div>
  );
}
