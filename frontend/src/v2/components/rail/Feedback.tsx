import clsx from 'clsx';
import type { ReactNode } from 'react';
import { hintCls } from '../styles';
import { RailSection } from './Section';

/**
 * An error the user has to read. Five rails spelled this out; one of them
 * (`RouteCheckRail`'s job-error line) had dropped `whitespace-pre-wrap`, so a
 * multi-line backend message collapsed onto one line there and nowhere else.
 *
 * Not Catalyst's `ErrorMessage`: that one is `sm:text-sm/6`, which is a
 * different size from every other line in a rail.
 */
export function RailError({ children, className }: {
  children: ReactNode; className?: string;
}) {
  if (!children) return null;
  return (
    <p className={clsx(
      'whitespace-pre-wrap text-xs/5 text-red-600 dark:text-red-500', className)}
    >
      ⚠ {children}
    </p>
  );
}

/**
 * A blocking state above the knobs it invalidates (slot 2) — a degraded import,
 * a failed job. Tinted rather than bare so it reads as a state, not a caption.
 */
export function RailAlert({ tone = 'warning', children }: {
  tone?: 'warning' | 'error'; children: ReactNode;
}) {
  if (!children) return null;
  return (
    <p className={clsx(hintCls, 'rounded-md p-2',
      tone === 'error'
        ? 'bg-red-500/10 text-red-700 dark:text-red-400'
        : 'bg-amber-500/10 text-amber-700 dark:text-amber-400')}
    >
      {children}
    </p>
  );
}

/**
 * Slot 7. What the active paint reported — the `stats` half of `PaintInfo`.
 *
 * Four rails rendered this with the same copy and slightly different markup.
 * The `whitespace-pre-wrap` class is a contract: `frontend/v2-smoke.mjs` finds
 * the stats paragraph by taking the FIRST `.whitespace-pre-wrap` in the
 * document, so this must keep the class and must not be preceded by another
 * one — which is why `RailError` sits above it only when there is an error to
 * show, and returns null otherwise.
 */
export function RailStats({ label = 'In view', text, error, empty, children }: {
  label?: string;
  text?: string | null;
  error?: string | null;
  /** Shown when there is neither text nor error. */
  empty?: ReactNode;
  /** Extra content under the stats line (findings, lists). */
  children?: ReactNode;
}) {
  return (
    <RailSection title={label}>
      <RailError>{error}</RailError>
      {!error && text && (
        <p className={clsx('whitespace-pre-wrap', hintCls)}>{text}</p>
      )}
      {!error && !text && empty && <p className={hintCls}>{empty}</p>}
      {children}
    </RailSection>
  );
}
