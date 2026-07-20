import clsx from 'clsx';
import { useStore } from '../../state/store';
import { useActiveView } from './hooks';

const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';

/**
 * The right rail for a general view (BREP faces, STEP colors…). Views are
 * static — no threshold, nothing to run — so this is a minimal info panel:
 * the view's label/blurb plus the shared paint stats. Mirrors the minimal
 * shell of DirectionsRail; contrast with the check-oriented SettingsRail.
 */
export function ViewRail() {
  const view = useActiveView();
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  if (!view) return null;
  const Icon = view.icon;

  return (
    <div className="flex h-full w-72 shrink-0 flex-col gap-4 overflow-auto border-l border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900">
      <div>
        <div className="flex items-center gap-2">
          <Icon className="size-4 text-blue-600 dark:text-blue-400" />
          <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">{view.label}</h2>
        </div>
        <p className={clsx('mt-1', hintCls)}>{view.blurb}</p>
      </div>

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
    </div>
  );
}
