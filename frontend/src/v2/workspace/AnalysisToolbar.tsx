import { MoreHorizontal } from 'lucide-react';
import { useStore } from '../../state/store';
import {
  Tooltip, TooltipContent, TooltipTrigger,
} from '../components/ui/tooltip';
import { cn } from '../lib/utils';
import { useV2 } from '../store';
import { selectAnalysis, useActiveAnalysis, useVisibleAnalyses } from './hooks';
import { resultFor } from './hooks';

/**
 * The floating toolbar over the viewer (page 4a): one icon per runnable
 * analysis, the active one highlighted, a dot marking analyses that already
 * have a result. The "⋯" button reveals the advanced analyses.
 */
export function AnalysisToolbar() {
  const active = useActiveAnalysis();
  const analyses = useVisibleAnalyses();
  const manifest = useStore((s) => s.manifest);
  const advanced = useV2((s) => s.advanced);
  const setAdvanced = useV2((s) => s.setAdvanced);

  return (
    <div className="absolute left-1/2 top-3 flex -translate-x-1/2 items-center gap-1 rounded-lg border bg-background/90 p-1 shadow-md backdrop-blur">
      {analyses.map((a) => {
        const Icon = a.icon;
        const isActive = a.id === active.id;
        const computed = !!resultFor(manifest, a);
        return (
          <Tooltip key={a.id}>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => selectAnalysis(a)}
                className={cn(
                  'relative flex size-8 items-center justify-center rounded-md transition-colors',
                  isActive
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                )}
                aria-label={a.label}
                aria-pressed={isActive}
              >
                <Icon className="size-4" />
                {computed && !isActive && (
                  <span className="absolute bottom-1 right-1 size-1.5 rounded-full bg-success" />
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {a.label}
              {computed ? ' · computed' : ' · not run'}
            </TooltipContent>
          </Tooltip>
        );
      })}

      <span className="mx-0.5 h-5 w-px bg-border" />

      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={() => setAdvanced(!advanced)}
            className={cn(
              'flex size-8 items-center justify-center rounded-md transition-colors',
              advanced
                ? 'bg-accent text-accent-foreground'
                : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
            )}
            aria-label="Toggle advanced analyses"
            aria-pressed={advanced}
          >
            <MoreHorizontal className="size-4" />
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          {advanced ? 'Hide advanced analyses' : 'More analyses'}
        </TooltipContent>
      </Tooltip>
    </div>
  );
}
