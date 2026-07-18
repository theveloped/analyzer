import { COL } from '../../colorizers/core';
import { sequential, sequentialGradientCss } from '../../viewer/colormaps';
import type { RGB } from '../../registry/types';
import { useStore } from '../../state/store';
import { flyToFocus } from '../../viewer/controller';
import { useV2 } from '../store';
import { useActiveAnalysis } from './hooks';

const rgbCss = (c: RGB | readonly number[]) =>
  `rgb(${Math.round(c[0] * 255)} ${Math.round(c[1] * 255)} ${Math.round(c[2] * 255)})`;

const near = (a: RGB | readonly number[], b: RGB) =>
  Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2]) < 0.02;

/** Bottom-left legend for the active check. Scalar heatmaps get the field's
 * actual (perceptually-uniform) ramp with limit→worst ends; the qualitative
 * rows (ok / no-data) stay as labelled swatches. */
export function Legend() {
  const legend = useStore((s) => s.legend);
  const active = useActiveAnalysis();
  const params = useStore((s) => s.viewerParams[active.process]) ?? {};
  // re-render (and re-read the background-matched map) when the theme flips
  const theme = useV2((s) => s.theme);
  void theme;
  if (!legend.length) return null;

  const threshold = params[active.thresholdParam] ?? active.thresholdDefault;
  const severityEnd = sequential(1); // the ramp's top = the flagged swatch
  const rows = legend.filter((e) => !near(e.color, severityEnd));

  return (
    <div className="absolute bottom-3 left-3 w-[15rem] rounded-lg border bg-background/92 p-2.5 shadow-sm backdrop-blur">
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {active.label} · severity
      </div>

      <div
        className="h-2 w-full rounded ring-1 ring-black/10"
        style={{ background: sequentialGradientCss() }}
      />
      <div className="mt-1 mb-2 flex justify-between text-[10px] tabular-nums text-muted-foreground">
        <span>at limit · {threshold} {active.unit}</span>
        <span>most severe</span>
      </div>

      <div className="flex flex-col gap-1">
        {rows.map((entry, i) => {
          const label = near(entry.color, COL.inaccess) ? entry.label || 'no data' : entry.label;
          return (
            <button
              key={i}
              type="button"
              disabled={!entry.focus}
              onClick={entry.focus ? () => flyToFocus(entry.focus!) : undefined}
              className="flex items-center gap-2 text-left text-xs text-foreground enabled:hover:text-primary disabled:cursor-default"
              title={entry.focus ? 'click to view these faces' : undefined}
            >
              <span
                className="size-2.5 shrink-0 rounded-[3px] ring-1 ring-black/10"
                style={{ background: rgbCss(entry.color) }}
              />
              <span className="truncate">{label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
