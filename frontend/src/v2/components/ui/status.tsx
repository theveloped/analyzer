import {
  CheckCircle2, CircleDashed, CircleDot, OctagonAlert, TriangleAlert,
  type LucideIcon,
} from 'lucide-react';
import * as React from 'react';
import { cn } from '../../lib/utils';

/**
 * Status system (dataviz "status" job): a small fixed scale with reserved
 * meaning, always shipped as **icon + label**, never color alone — the icon's
 * shape carries the state so it survives colour-blind vision, grayscale and
 * the sub-3:1 light-surface contrast of `warning`/`serious`. Colours are the
 * validated, mode-invariant status tokens from app.css.
 */

export type StatusKind =
  | 'good' | 'warning' | 'serious' | 'critical' | 'active' | 'neutral';

const STATUS: Record<StatusKind, { cssVar: string; Icon: LucideIcon }> = {
  good: { cssVar: 'var(--status-good)', Icon: CheckCircle2 },
  warning: { cssVar: 'var(--status-warning)', Icon: TriangleAlert },
  serious: { cssVar: 'var(--status-serious)', Icon: TriangleAlert },
  critical: { cssVar: 'var(--status-critical)', Icon: OctagonAlert },
  active: { cssVar: 'var(--primary)', Icon: CircleDot },
  neutral: { cssVar: 'var(--muted-foreground)', Icon: CircleDashed },
};

/** Icon glyph in the status colour — shape + colour, so it reads without hue. */
export function StatusDot({
  status, className,
}: { status: StatusKind; className?: string }) {
  const { cssVar, Icon } = STATUS[status];
  return <Icon className={cn('size-3.5 shrink-0', className)} style={{ color: cssVar }} />;
}

/** Tinted pill: status colour on the icon, label in ink (text wears text
 * tokens, never the status colour). */
export function StatusBadge({
  status, children, className,
}: { status: StatusKind; children: React.ReactNode; className?: string }) {
  const { cssVar, Icon } = STATUS[status];
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium text-foreground',
        className,
      )}
      style={{ backgroundColor: `color-mix(in oklab, ${cssVar} 16%, transparent)` }}
    >
      <Icon className="size-3" style={{ color: cssVar }} />
      {children}
    </span>
  );
}
