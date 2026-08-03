import clsx from 'clsx';
import type { ReactNode } from 'react';
import { useStore } from '../../../state/store';
import { hintCls } from '../../components/styles';

/**
 * Pieces the injection mode-rails share.
 *
 * These rails exist because their modes carry logic no declaration can hold —
 * a job whose analysis params are not its viewer params, or a viewer click
 * that means something. What they have in common is the shape underneath:
 * read the injection viewer bag, write one key, and offer a list of things you
 * can select or remove.
 */

const EMPTY: Record<string, unknown> = {};

/** Result by selector index; -1 / unset = latest. The manifest lists results
 * oldest -> newest, so a recompute lands last. Mirrors the paints' own
 * `pickResult`, which is what the selector index means to them. */
export function pickResult<T>(
  list: T[], index: number | null | undefined,
): T | undefined {
  if (index != null && index >= 0 && index < list.length) return list[index];
  return list[list.length - 1];
}

/** The injection viewer-param bag plus a one-key setter. */
export function useInjectionParams() {
  const params = useStore((s) => s.viewerParams.injection_molding) ?? EMPTY;
  const setParam = useStore((s) => s.setViewerParam);
  return {
    params,
    set: (name: string, value: unknown) =>
      setParam('injection_molding', name, value),
  };
}

/**
 * A chip list — ranked sprue proposals, placed ejector pins. Replaces the v1
 * `.proposal-list`, whose one styling hook was a bare `selected` class.
 */
export function ChipList({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap gap-1">{children}</div>;
}

export function Chip({ selected, tone = 'neutral', title, onClick, children }: {
  selected?: boolean;
  tone?: 'neutral' | 'bad';
  title?: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-pressed={selected}
      onClick={onClick}
      className={clsx(
        'rounded-md px-2 py-1 text-[11px]/4 font-medium transition',
        selected
          ? 'bg-blue-600 text-white'
          : tone === 'bad'
            ? 'bg-red-500/10 text-red-700 hover:bg-red-500/20 dark:text-red-400'
            : 'bg-zinc-950/5 text-zinc-700 hover:bg-zinc-950/10 dark:bg-white/10 dark:text-zinc-300 dark:hover:bg-white/15')}
    >
      {children}
    </button>
  );
}

/** The "click the part to…" line every pick-driven rail needs. */
export function PickHint({ children }: { children: ReactNode }) {
  return <p className={clsx(hintCls, 'italic')}>{children}</p>;
}
