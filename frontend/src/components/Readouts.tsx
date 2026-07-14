import { useStore } from '../state/store';
import { flyToFocus } from '../viewer/controller';

export function Legend() {
  const legend = useStore((s) => s.legend);
  return (
    <div className="legend">
      {legend.map((entry, i) => (
        <div
          key={i}
          style={entry.focus ? { cursor: 'pointer' } : undefined}
          title={entry.focus ? 'click to view these faces' : undefined}
          onClick={entry.focus ? () => flyToFocus(entry.focus!) : undefined}
        >
          <span
            className="chip"
            style={{ background: `rgb(${entry.color.map((x) => Math.round(x * 255)).join(',')})` }}
          />
          {entry.label}
        </div>
      ))}
    </div>
  );
}

export function StatsBar() {
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  if (!stats && !error) return null;
  return <div className="stats">{error ? `⚠ ${error}` : stats}</div>;
}

export function InspectPanel() {
  const pick = useStore((s) => s.pick);
  return <div className="pick">{pick}</div>;
}
