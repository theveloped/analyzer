import clsx from 'clsx';
import { CheckCircle2, Crosshair, MoreHorizontal } from 'lucide-react';
import { useStore } from '../../state/store';
import { useV2 } from '../store';
import { activateDirections, resultFor, selectAnalysis, useActiveAnalysis, useDirectionsActive, useVisibleAnalyses } from './hooks';

/**
 * The floating toolbar over the viewer: one icon per runnable analysis, the
 * active one highlighted, a check marking analyses that already have a result.
 * The "⋯" button reveals the advanced analyses. Catalyst ships no tooltip, so
 * hints use native `title` (matches wf-api).
 */
export function AnalysisToolbar() {
  const active = useActiveAnalysis();
  const analyses = useVisibleAnalyses();
  const manifest = useStore((s) => s.manifest);
  const advanced = useV2((s) => s.advanced);
  const setAdvanced = useV2((s) => s.setAdvanced);
  const inDirections = useDirectionsActive();

  return (
    <div className="absolute left-1/2 top-3 flex -translate-x-1/2 items-center gap-1 rounded-xl border border-zinc-950/10 bg-white/90 p-1 shadow-lg ring-1 ring-zinc-950/5 backdrop-blur dark:border-white/10 dark:bg-zinc-800/90 dark:ring-white/10">
      {/* candidate-directions view — the orientations every check runs from */}
      <button
        type="button"
        onClick={activateDirections}
        title="Candidate directions"
        aria-pressed={inDirections}
        className={clsx(
          'flex size-8 items-center justify-center rounded-lg transition',
          inDirections
            ? 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900'
            : 'text-zinc-500 hover:bg-zinc-950/5 hover:text-zinc-950 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-white',
        )}
      >
        <Crosshair className="size-4" />
      </button>
      <span className="mx-0.5 h-5 w-px bg-zinc-950/10 dark:bg-white/10" />

      {analyses.map((a) => {
        const Icon = a.icon;
        const isActive = !inDirections && a.id === active.id;
        const computed = !!resultFor(manifest, a);
        return (
          <button
            key={a.id}
            type="button"
            onClick={() => selectAnalysis(a)}
            title={`${a.label}${computed ? ' · computed' : ' · not run'}`}
            aria-pressed={isActive}
            className={clsx(
              'relative flex size-8 items-center justify-center rounded-lg transition',
              isActive
                ? 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900'
                : 'text-zinc-500 hover:bg-zinc-950/5 hover:text-zinc-950 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-white',
            )}
          >
            <Icon className="size-4" />
            {computed && !isActive && (
              <CheckCircle2
                className="absolute -bottom-0.5 -right-0.5 size-2.5 rounded-full bg-white dark:bg-zinc-800"
                style={{ color: 'var(--status-good)' }}
              />
            )}
          </button>
        );
      })}

      <span className="mx-0.5 h-5 w-px bg-zinc-950/10 dark:bg-white/10" />

      <button
        type="button"
        onClick={() => setAdvanced(!advanced)}
        title={advanced ? 'Hide advanced analyses' : 'More analyses'}
        aria-pressed={advanced}
        className={clsx(
          'flex size-8 items-center justify-center rounded-lg transition',
          advanced
            ? 'bg-zinc-950/5 text-zinc-950 dark:bg-white/10 dark:text-white'
            : 'text-zinc-500 hover:bg-zinc-950/5 hover:text-zinc-950 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-white',
        )}
      >
        <MoreHorizontal className="size-4" />
      </button>
    </div>
  );
}
