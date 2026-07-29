import { useRef, type ReactNode } from 'react';
import { useV2 } from '../store';

/**
 * The right column: one wrapper owning the border, the scroll box and a
 * drag-to-resize handle, so the rails themselves are just content.
 *
 * The width has to be a runtime style — Tailwind cannot emit an arbitrary
 * one — which is why the chrome is hoisted here rather than repeated with a
 * `w-72` in every rail. Width is remembered PER RAIL: the study table wants
 * far more room than a settings panel, and a single shared number would make
 * every rail as wide as the widest.
 *
 * Nothing else in the layout needs to react: the viewer column is
 * `min-w-0 flex-1` and its ResizeObserver keeps the canvas in step mid-drag.
 */

export const RAIL_MIN = 240;
export const RAIL_MAX = 1100;

export function RightRail({ id, defaultWidth = 288, children }: {
  id: string; defaultWidth?: number; children: ReactNode;
}) {
  const stored = useV2((s) => s.railWidths[id]);
  const setRailWidth = useV2((s) => s.setRailWidth);
  const width = stored ?? defaultWidth;
  const drag = useRef<{ startX: number; startWidth: number } | null>(null);

  function onPointerDown(event: React.PointerEvent) {
    event.preventDefault();
    (event.target as Element).setPointerCapture(event.pointerId);
    drag.current = { startX: event.clientX, startWidth: width };
  }

  function onPointerMove(event: React.PointerEvent) {
    if (!drag.current) return;
    // right-hand rail: dragging the handle left makes it WIDER, so the
    // delta inverts
    const next = drag.current.startWidth - (event.clientX - drag.current.startX);
    setRailWidth(id, Math.min(RAIL_MAX, Math.max(RAIL_MIN, next)));
  }

  function onPointerUp(event: React.PointerEvent) {
    drag.current = null;
    (event.target as Element).releasePointerCapture(event.pointerId);
  }

  return (
    <div
      className="relative flex h-full shrink-0 border-l border-zinc-950/5 bg-white dark:border-white/10 dark:bg-zinc-900"
      style={{ width }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the panel"
        title="Drag to resize"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={() => setRailWidth(id, defaultWidth)}
        className="absolute inset-y-0 -left-1 z-10 w-2 cursor-col-resize touch-none after:absolute after:inset-y-0 after:left-1/2 after:w-px after:-translate-x-1/2 after:bg-transparent hover:after:bg-blue-500"
      />
      <div className="flex h-full min-w-0 flex-1 flex-col overflow-auto">
        {children}
      </div>
    </div>
  );
}
