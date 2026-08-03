import clsx from 'clsx';
import type { ReactNode } from 'react';
import { Checkbox } from '../../../catalyst/checkbox';
import { Switch } from '../../../catalyst/switch';
import { hintCls, labelCls } from '../styles';

/**
 * The form row vocabulary. Deliberately built against `labelCls`/`hintCls`
 * rather than Catalyst's `Field`/`Label`/`Description`: those are `sm:`
 * breakpointed against the WINDOW, not the rail, so they render a size larger
 * below 640 px, and `Description` is `sm:text-sm/6` where a rail hint is
 * `text-xs/5` — a genuinely different size, not a breakpoint quirk.
 */

/** A labelled control. `unit` sits with the label, where it reads as part of
 * the question rather than as a suffix on the answer. */
export function RailField({ label, unit, hint, htmlFor, children }: {
  label: string;
  unit?: string;
  hint?: ReactNode;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <label className={clsx(labelCls, 'block')} htmlFor={htmlFor}>
        {label}
        {unit && <span className="font-normal text-zinc-400"> ({unit})</span>}
      </label>
      <div className="mt-1 min-w-0">{children}</div>
      {hint && <p className={clsx('mt-1', hintCls)}>{hint}</p>}
    </div>
  );
}

/**
 * A setting that is on or off. A SWITCH — the thing you flip to change how the
 * tool behaves. Contrast `RailCheckbox`.
 */
export function RailBool({ label, hint, checked, onChange }: {
  label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <div className={labelCls}>{label}</div>
        {hint && <p className={hintCls}>{hint}</p>}
      </div>
      <Switch checked={checked} onChange={onChange} aria-label={label} />
    </div>
  );
}

/**
 * A modifier on the thing next to it — "invert this term", "include pockets".
 * A CHECKBOX, not a switch: it qualifies a statement rather than toggling a
 * behaviour, and it sits inline with the text it modifies. Two rails were
 * hand-rolling `<input type="checkbox">` while Catalyst's sat unused.
 */
export function RailCheckbox({ label, checked, onChange, title }: {
  label: ReactNode; checked: boolean; onChange: (v: boolean) => void;
  /** What ticking it does, when the label alone cannot say it. */
  title?: string;
}) {
  return (
    <label title={title}
      className="flex items-center gap-2 text-[11px]/5 text-zinc-500 dark:text-zinc-400">
      <Checkbox checked={checked} onChange={onChange} />
      {label}
    </label>
  );
}

/** An exclusive choice small enough to show every option at once — a section
 * axis, a measurement frame. Larger sets belong in a Select. */
export function RailSegmented<T extends string>({ options, value, onChange, ariaLabel }: {
  options: { id: T; label: string; title?: string }[];
  value: T;
  onChange: (id: T) => void;
  ariaLabel?: string;
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="flex gap-1 rounded-lg bg-zinc-950/5 p-0.5 dark:bg-white/10"
    >
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          title={option.title}
          aria-pressed={value === option.id}
          onClick={() => onChange(option.id)}
          className={clsx(
            'flex-1 rounded-md px-1 py-1 text-xs font-medium transition',
            value === option.id
              ? 'bg-white text-zinc-950 shadow-sm dark:bg-zinc-700 dark:text-white'
              : 'text-zinc-500 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white')}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
