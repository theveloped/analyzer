import { CircleDashed, Plus } from 'lucide-react';
import { useStore } from '../../state/store';
import type { Analysis } from '../analyses';
import { Button } from '../components/ui/button';
import { StatusDot } from '../components/ui/status';
import { cn } from '../lib/utils';
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
  // re-render when the manifest/params change so summaries + dots stay live
  const manifestVersion = useStore((s) => s.manifestVersion);
  const viewerParams = useStore((s) => s.viewerParams);
  void manifestVersion;
  void viewerParams;
  const manifest = useStore((s) => s.manifest);

  return (
    <div className="flex h-full w-60 shrink-0 flex-col gap-2 overflow-auto border-r bg-muted/30 p-3">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        Checks
      </div>
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
                className={cn(
                  'w-full rounded-md border p-2.5 text-left transition-colors',
                  isActive
                    ? 'border-primary/40 bg-primary/5 ring-1 ring-primary/20'
                    : 'border-transparent hover:bg-accent',
                )}
              >
                <div className="flex items-center gap-2">
                  <StatusDot
                    status={computed ? 'good' : isActive ? 'active' : 'neutral'}
                  />
                  <Icon className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="flex-1 text-sm font-medium">{a.label}</span>
                  {a.tier === 'advanced' && (
                    <span className="text-[9px] uppercase tracking-wide text-muted-foreground">adv</span>
                  )}
                </div>
                <div className="ml-[22px] mt-1 text-xs text-muted-foreground">
                  {stepSummary(a)}
                </div>
              </button>
              {i < analyses.length - 1 && (
                <div className="ml-[17px] h-2.5 w-px bg-border" />
              )}
            </div>
          );
        })}
      </div>

      <Button variant="outline" size="sm" className="mt-1 border-dashed" disabled>
        <Plus className="size-3.5" /> Add check
      </Button>

      <div className="mt-3 flex items-center gap-2 rounded-md bg-muted/50 p-2 text-xs text-muted-foreground">
        <CircleDashed className="size-3.5" />
        More checks (draft, mold flow…) land here as we build them out.
      </div>
    </div>
  );
}
