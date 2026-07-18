import { useStore } from '../../state/store';
import { flyToFocus } from '../../viewer/controller';
import { useActiveAnalysis } from './hooks';

/** Bottom-left legend for the active view, fed by the shared store's legend
 * entries (click a row with a focus to fly the camera there). */
export function Legend() {
  const legend = useStore((s) => s.legend);
  const active = useActiveAnalysis();
  if (!legend.length) return null;
  return (
    <div className="absolute bottom-3 left-3 max-w-[15rem] rounded-lg border bg-background/92 p-2.5 shadow-sm backdrop-blur">
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {active.label} · {active.unit}
      </div>
      <div className="flex flex-col gap-1">
        {legend.map((entry, i) => (
          <button
            key={i}
            type="button"
            disabled={!entry.focus}
            onClick={entry.focus ? () => flyToFocus(entry.focus!) : undefined}
            className="flex items-center gap-2 text-left text-xs text-foreground enabled:hover:text-primary disabled:cursor-default"
            title={entry.focus ? 'click to view these faces' : undefined}
          >
            <span
              className="size-2.5 shrink-0 rounded-full"
              style={{ background: `rgb(${entry.color.map((x) => Math.round(x * 255)).join(',')})` }}
            />
            <span className="truncate">{entry.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
