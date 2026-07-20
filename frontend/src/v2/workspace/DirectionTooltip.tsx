import { Trash2, X } from 'lucide-react';
import { currentDirections } from '../../processes/directions/build';
import { PROVENANCE_LABELS } from '../../processes/directions/modes';
import { provenanceCss } from '../../processes/directions/state';
import { useDirectionSetup } from '../../processes/directions/useSetup';

/**
 * Floating card shown when an arrow is clicked: every provenance that
 * contributed the direction, its vector, and a delete (hide) control.
 * Position is the click point in screen space; it dismisses on any click that
 * misses an arrow (the scene sets selectedArrow to null).
 */
export function DirectionTooltip() {
  const { setup, patch, params, setParam } = useDirectionSetup();
  const sel = params.selectedArrow as { index: number; x: number; y: number } | null;
  if (!sel) return null;
  const dir = currentDirections[sel.index];
  if (!dir) return null;

  const close = () => {
    setParam('directions', 'selectedArrow', null);
    setParam('directions', 'highlightBrep', []);
  };
  const remove = () => {
    patch({ suppressed: [...setup.suppressed, dir.key] });
    close();
  };

  // fixed to the viewport at the click point, so it sits next to the arrow
  const left = Math.min(sel.x + 12, window.innerWidth - 260);
  const top = Math.min(sel.y + 12, window.innerHeight - 200);

  return (
    <div
      className="fixed z-20 w-60 rounded-lg border border-zinc-950/10 bg-white/95 p-3 shadow-xl ring-1 ring-zinc-950/5 backdrop-blur dark:border-white/10 dark:bg-zinc-800/95 dark:ring-white/10"
      style={{ left, top }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="text-xs/5 font-semibold text-zinc-950 dark:text-white">Direction</div>
        <button type="button" aria-label="close" onClick={close}
          className="text-zinc-400 hover:text-zinc-950 dark:hover:text-white">
          <X className="size-3.5" />
        </button>
      </div>

      <div className="mt-1 font-mono text-xs/5 text-zinc-600 dark:text-zinc-300">
        [{dir.vector.map((c) => c.toFixed(3)).join(', ')}]
      </div>

      <div className="mt-2 mb-1 text-[10px] uppercase tracking-wide text-zinc-400">
        {dir.provenances.length > 1 ? 'Provenances' : 'Provenance'}
      </div>
      <ul className="flex flex-col gap-1">
        {dir.provenances.map((p, i) => (
          <li key={i} className="flex items-center gap-2 text-xs/5 text-zinc-700 dark:text-zinc-300">
            <span className="size-2.5 shrink-0 rounded-full ring-1 ring-inset ring-zinc-950/10 dark:ring-white/20"
              style={{ backgroundColor: provenanceCss(p.source) }} />
            <span className="flex-1">{PROVENANCE_LABELS[p.source]}</span>
            <span className="text-zinc-400">{p.label}</span>
          </li>
        ))}
      </ul>

      <button type="button" onClick={remove}
        className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-md bg-red-600/10 py-1.5 text-xs/5 font-medium text-red-600 hover:bg-red-600/20 dark:text-red-400">
        <Trash2 className="size-3.5" /> Delete direction
      </button>
    </div>
  );
}
