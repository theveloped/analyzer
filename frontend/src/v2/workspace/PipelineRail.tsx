import clsx from 'clsx';
import { CircleDashed, Plus } from 'lucide-react';
import { Button } from '../../catalyst/button';
import { useStore } from '../../state/store';
import type { Analysis } from '../analyses';
import { StatusDot } from '../components/status';
import { resultFor, selectAnalysis, useActiveAnalysis, useVisibleAnalyses } from './hooks';

function stepSummary(a: Analysis): string {
  const { manifest, viewerParams } = useStore.getState();
  const result = resultFor(manifest, a);
  const params = viewerParams[a.process] ?? {};
  const threshold = params[a.thresholdParam] ?? a.thresholdDefault;
  if (result) {
    const s = result.stats as Record<string, number>;
    const min = typeof s.min === 'number' ? `min ${s.min.toFixed(2)} ${a.unit}` : 'computed';
    const p50 = typeof s.p50 === 'number' ? ` · p50 ${s.p50.toFixed(2)} ${a.unit}` : '';
    return `${min}${p50}`;
  }
  return `not run · limit ${threshold} ${a.unit}`;
}

export function PipelineRail() {
  const active = useActiveAnalysis();
  const analyses = useVisibleAnalyses();
  const manifestVersion = useStore((s) => s.manifestVersion);
  const viewerParams = useStore((s) => s.viewerParams);
  void manifestVersion;
  void viewerParams;
  const manifest = useStore((s) => s.manifest);

  return (
    <div className="flex h-full w-64 shrink-0 flex-col gap-3 overflow-auto border-r border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900">
      <div className="text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">Checks</div>
      <div className="flex flex-col">
        {analyses.map((a, i) => {
          const isActive = a.id === active.id;
          const computed = !!resultFor(manifest, a);
          const Icon = a.icon;
          return (
            <div key={a.id}>
              <button
                type="button"
                onClick={() => selectAnalysis(a)}
                className={clsx(
                  'w-full rounded-lg border p-2.5 text-left transition',
                  isActive
                    ? 'border-blue-500/30 bg-blue-500/5'
                    : 'border-transparent hover:bg-zinc-950/5 dark:hover:bg-white/5',
                )}
              >
                <div className="flex items-center gap-2">
                  <StatusDot status={computed ? 'good' : isActive ? 'active' : 'neutral'} />
                  <Icon className="size-3.5 shrink-0 text-zinc-500 dark:text-zinc-400" />
                  <span className="flex-1 text-sm/5 font-medium text-zinc-950 dark:text-white">{a.label}</span>
                  {a.tier === 'advanced' && (
                    <span className="text-[10px] uppercase tracking-wide text-zinc-400">adv</span>
                  )}
                </div>
                <div className="ml-[22px] mt-1 text-xs/5 text-zinc-500 dark:text-zinc-400">
                  {stepSummary(a)}
                </div>
              </button>
              {i < analyses.length - 1 && (
                <div className="ml-[17px] h-2.5 w-px bg-zinc-950/10 dark:bg-white/10" />
              )}
            </div>
          );
        })}
      </div>

      <Button outline disabled className="w-full">
        <Plus data-slot="icon" /> Add check
      </Button>

      <div className="mt-2 flex items-start gap-2 rounded-lg bg-zinc-950/2.5 p-2.5 text-xs/5 text-zinc-500 dark:bg-white/5 dark:text-zinc-400">
        <CircleDashed className="mt-0.5 size-3.5 shrink-0" />
        More checks (draft, mold flow…) land here as we build them out.
      </div>
    </div>
  );
}
