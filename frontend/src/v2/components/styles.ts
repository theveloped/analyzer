// Class strings shared across the rails.
//
// There is no CSS framework here (see AGENTS.md), so a "rail hint paragraph"
// is a literal string of Tailwind tokens. That string was declared verbatim in
// a dozen files, which is fine right up until one of them is restyled and the
// panels stop matching. These are the ones that name a ROLE — anything used in
// exactly one place stays inline where it is read.

/** Secondary explanatory text under a control or heading. */
export const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';

/** A field label above an input. */
export const labelCls = 'text-sm/6 font-medium text-zinc-950 dark:text-white';

/** In-table/inline buttons: keyboard focus stays visible, mouse focus does
 * not — clicking a value should act on it, not leave a ring behind. */
export const focusCls = 'focus:outline-none focus-visible:rounded-xs '
  + 'focus-visible:outline-2 focus-visible:outline-blue-500';
