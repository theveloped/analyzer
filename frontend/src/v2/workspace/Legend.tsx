import { COL } from '../../colorizers/core';
import type { LegendEntry, RGB } from '../../registry/types';
import { useStore } from '../../state/store';
import { flyToFocus, selectLegendGroup } from '../../viewer/controller';
import { useActiveAnalysis, useActiveLens, useCheckActive, useDirectionsActive } from './hooks';

const rgbCss = (c: RGB | readonly number[]) =>
  `rgb(${Math.round(c[0] * 255)} ${Math.round(c[1] * 255)} ${Math.round(c[2] * 255)})`;

const box =
  'absolute bottom-3 left-3 w-60 rounded-lg border border-zinc-950/10 bg-white/90 p-2.5 shadow-lg ring-1 ring-zinc-950/5 backdrop-blur dark:border-white/10 dark:bg-zinc-800/90 dark:ring-white/10';
const title = 'mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400';
const sub = 'text-[10px] tabular-nums text-zinc-500 dark:text-zinc-400';

/** Bottom-left legend. Heatmaps render a colorbar spanning the real data range
 * (min→max, or symmetric with 0 centred for a diverging field); everything else
 * falls back to the discrete swatch list. */
export function Legend() {
  const colorbar = useStore((s) => s.colorbar);
  const legend = useStore((s) => s.legend);
  const selection = useStore((s) => s.selection);
  // clicking a row flies to the group AND selects it (fit-selection /
  // isolate / ghost act on the selection); clicking again deselects
  const pick = (entry: LegendEntry) => {
    flyToFocus(entry.focus!);
    if (!entry.focus?.faces?.length) return;
    const already = selection?.label === entry.label;
    selectLegendGroup(entry.label, already ? null : entry.focus.faces);
  };
  const activeAnalysis = useActiveAnalysis();
  const activeLens = useActiveLens();
  const checkActive = useCheckActive();
  const directionsActive = useDirectionsActive();
  // title follows what actually painted: the check when one is active,
  // otherwise the active lens (useActiveAnalysis falls back to thickness)
  const active = checkActive ? activeAnalysis
    : directionsActive ? { label: 'Candidate directions' }
    : activeLens ?? activeAnalysis;

  if (colorbar) {
    const { min, max, unit, diverging, gradient, threshold } = colorbar;
    const span = max - min || 1;
    const pct = (v: number) => Math.max(0, Math.min(100, ((v - min) / span) * 100));
    const showLimit = threshold != null && threshold > min && threshold < max;
    return (
      <div className={box}>
        <div className={title}>{active.label}{unit ? ` · ${unit}` : ''}</div>

        <div className="relative">
          <div className="h-2.5 w-full rounded ring-1 ring-black/10" style={{ background: gradient }} />
          {diverging && (
            <div className="pointer-events-none absolute -top-0.5 left-1/2 h-3.5 w-px -translate-x-1/2 bg-zinc-900 dark:bg-white" />
          )}
          {showLimit && (
            <div
              className="pointer-events-none absolute -top-0.5 h-3.5 w-px bg-zinc-900 dark:bg-white"
              style={{ left: `${pct(threshold!)}%` }}
              title={`limit ${threshold} ${unit ?? ''}`}
            />
          )}
        </div>

        <div className={`relative mt-1 flex justify-between ${sub}`}>
          <span>{min.toFixed(2)}</span>
          {diverging && <span className="absolute left-1/2 -translate-x-1/2">0</span>}
          <span>{max.toFixed(2)}</span>
        </div>

        {showLimit && (
          <div className={`mt-1 ${sub}`}>▎limit {threshold} {unit}</div>
        )}
        <div className="mt-1.5 flex items-center gap-2 text-xs/5 text-zinc-500 dark:text-zinc-400">
          <span className="size-2.5 rounded-[3px] ring-1 ring-black/10" style={{ background: rgbCss(COL.inaccess) }} />
          no data
        </div>
      </div>
    );
  }

  if (!legend.length) return null;
  return (
    <div className={box}>
      <div className={title}>{active.label}</div>
      <div className="flex flex-col gap-1">
        {legend.map((entry, i) => (
          <button
            key={i}
            type="button"
            disabled={!entry.focus}
            onClick={entry.focus ? () => pick(entry) : undefined}
            className={`flex items-center gap-2 text-left text-xs/5 enabled:hover:text-blue-600 disabled:cursor-default dark:enabled:hover:text-blue-400 ${
              selection?.label === entry.label
                ? 'font-semibold text-blue-600 dark:text-blue-400'
                : 'text-zinc-950 dark:text-white'
            }`}
            title={entry.focus ? 'click to view + select these faces' : undefined}
          >
            <span
              className="size-2.5 shrink-0 rounded-[3px] ring-1 ring-black/10"
              style={{ background: rgbCss(entry.color) }}
            />
            <span className="truncate">{entry.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
