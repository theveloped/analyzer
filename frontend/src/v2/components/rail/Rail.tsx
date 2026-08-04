import clsx from 'clsx';
import { X, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { Button } from '../../../catalyst/button';
import { StatusBadge, type StatusKind } from '../status';
import { hintCls } from '../styles';

/**
 * The rail skeleton. Every panel in the right column — and the pipeline on the
 * left — is these slots in this order:
 *
 *   1 Identity   icon · title · status · blurb · actions/close   (required)
 *   2 Alert      a blocking state that invalidates what follows
 *   3 Params     the primary knobs
 *   4 Advanced   collapsed compute knobs
 *   5 Action     the one primary button
 *   6 Findings   verdict-bearing output
 *   7 Readout    non-verdict output: stats, inspect, tables
 *
 * Only 3 → 5 → 6 are ordered by meaning: they are the workspace's stated
 * primary surface, "thresholds → run → findings" (v2/README.md). Advanced sits
 * ABOVE the action rather than below it because compute knobs are inputs to the
 * run — a Run button above them reads as "run, then configure".
 *
 * `RightRail` owns the border, the scroll box and the width. These own the
 * padding and the rhythm; nothing here draws chrome.
 */

/** The rail body. `gap-4` is the rhythm; a table-bodied rail passes `gap={3}`
 * because Catalyst's Table brings its own spacing. */
export function Rail({ gap = 4, className, children }: {
  gap?: 3 | 4; className?: string; children: ReactNode;
}) {
  return (
    <div
      className={clsx('flex min-h-full min-w-0 flex-col p-4',
        gap === 3 ? 'gap-3' : 'gap-4', className)}
    >
      {children}
    </div>
  );
}

/**
 * Slot 1. The single place a surface is named.
 *
 * The `<h2>` markup is load-bearing beyond looks: `frontend/v2-smoke.mjs`
 * locates the Section and Measure rails by `h2` text, and the close button by a
 * `title` starting with "Close". Keep both.
 */
export function RailHeader({
  icon: Icon, title, status, statusLabel, blurb, actions, onClose,
  closeTitle = 'Close',
}: {
  icon?: LucideIcon;
  title: string;
  status?: StatusKind;
  statusLabel?: ReactNode;
  blurb?: ReactNode;
  /** Trailing controls (an Export link, an Edit affordance). */
  actions?: ReactNode;
  onClose?: () => void;
  closeTitle?: string;
}) {
  // The title gets its own row. A rail is 288 px by default and 240 at its
  // narrowest; title + badge + an action on one line wraps the title onto two
  // lines while the badge sits alone on the first — the name of the thing loses
  // to its status, which is backwards. Status and actions get a second row, and
  // that row only exists when there is something on it.
  const meta = (status && statusLabel != null) || actions;
  return (
    <div>
      <div className="flex min-w-0 items-start gap-2">
        {Icon && (
          <Icon className="mt-0.5 size-4 shrink-0 text-blue-600 dark:text-blue-400" />
        )}
        <h2 className="min-w-0 flex-1 text-sm/6 font-semibold text-zinc-950 dark:text-white">
          {title}
        </h2>
        {onClose && (
          <Button plain onClick={onClose} title={closeTitle} aria-label={closeTitle}>
            <X data-slot="icon" />
          </Button>
        )}
      </div>
      {meta && (
        <div className="mt-1 flex min-w-0 items-center justify-between gap-2">
          {status && statusLabel != null ? (
            <StatusBadge status={status}>{statusLabel}</StatusBadge>
          ) : <span />}
          {actions}
        </div>
      )}
      {blurb && <p className={clsx('mt-1', hintCls)}>{blurb}</p>}
    </div>
  );
}
