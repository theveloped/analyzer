import clsx from 'clsx';
import type { ReactNode } from 'react';
import { Divider } from '../../../catalyst/divider';

/**
 * A titled group. This replaced eight declarations of the same label class
 * across eight files — including `PmiRail`'s, which baked the margin into the
 * constant and then stripped it back off with `.replace('mb-1.5 ', '')` at one
 * call site. The margin belongs to the section, not to the label, so that
 * cannot happen here.
 *
 * `micro` is the uppercase variant the measure/section tool rails use. It is
 * kept as a variant rather than normalized away because those two panels read
 * as instrument readouts, not settings — but they now share one component with
 * everything else.
 */
export function RailSection({ title, variant = 'default', action, className, children }: {
  title?: string;
  variant?: 'default' | 'micro';
  /** Trailing control on the title row (a clear link, a toggle). */
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={clsx('min-w-0', className)}>
      {title && (
        <div className={clsx('mb-1.5 flex items-center justify-between gap-2',
          variant === 'micro'
            ? 'text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400'
            : 'text-xs/5 font-medium text-zinc-500 dark:text-zinc-400')}
        >
          <span className="min-w-0 truncate">{title}</span>
          {action}
        </div>
      )}
      {children}
    </div>
  );
}

/** Catalyst's `Divider` renders identically to the `h-px bg-…` div seven rails
 * were spelling out by hand, and was sitting vendored and unused. */
export function RailDivider({ soft, className }: {
  soft?: boolean; className?: string;
}) {
  return <Divider soft={soft} className={className} />;
}
