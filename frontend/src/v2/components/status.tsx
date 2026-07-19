import clsx from 'clsx';
import {
  CheckCircle2, CircleDashed, CircleDot, OctagonAlert, TriangleAlert,
  type LucideIcon,
} from 'lucide-react';
import type { ReactNode } from 'react';

/**
 * App-level status system (kept from the dataviz work): a small fixed scale
 * shipped as icon + label, never colour alone. Colours are the validated,
 * mode-invariant `--status-*` tokens from app.css; interaction states borrow
 * Tailwind's zinc/blue so they sit naturally in the Catalyst palette.
 */

export type StatusKind =
  | 'good' | 'warning' | 'serious' | 'critical' | 'active' | 'neutral';

const STATUS: Record<StatusKind, { cssVar: string; Icon: LucideIcon }> = {
  good: { cssVar: 'var(--status-good)', Icon: CheckCircle2 },
  warning: { cssVar: 'var(--status-warning)', Icon: TriangleAlert },
  serious: { cssVar: 'var(--status-serious)', Icon: TriangleAlert },
  critical: { cssVar: 'var(--status-critical)', Icon: OctagonAlert },
  active: { cssVar: 'var(--color-blue-600)', Icon: CircleDot },
  neutral: { cssVar: 'var(--color-zinc-400)', Icon: CircleDashed },
};

export function StatusDot({ status, className }: { status: StatusKind; className?: string }) {
  const { cssVar, Icon } = STATUS[status];
  return <Icon className={clsx('size-3.5 shrink-0', className)} style={{ color: cssVar }} />;
}

export function StatusBadge({
  status, children, className,
}: { status: StatusKind; children: ReactNode; className?: string }) {
  const { cssVar, Icon } = STATUS[status];
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs/5 font-medium text-zinc-700 dark:text-zinc-300',
        className,
      )}
      style={{ backgroundColor: `color-mix(in oklab, ${cssVar} 15%, transparent)` }}
    >
      <Icon className="size-3" style={{ color: cssVar }} />
      {children}
    </span>
  );
}
