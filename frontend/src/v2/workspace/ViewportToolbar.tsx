import clsx from 'clsx';
import {
  Circle, Cuboid, Layers, Maximize, Ruler, Scan, Slice, Spline, Triangle,
} from 'lucide-react';
import { fitPart } from '../../viewer/controller';
import type { RenderStyle } from '../../viewer/viewportState';
import { useV2 } from '../store';

const btnCls = 'flex size-8 items-center justify-center rounded-lg transition';
const activeCls = 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900';
const idleCls = 'text-zinc-500 hover:bg-zinc-950/5 hover:text-zinc-950 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-white';
const disabledCls = 'cursor-not-allowed text-zinc-300 dark:text-zinc-600';

function Divider() {
  return <span className="mx-0.5 h-5 w-px bg-zinc-950/10 dark:bg-white/10" />;
}

const STYLES: { id: RenderStyle; label: string; Icon: typeof Circle }[] = [
  { id: 'shaded', label: 'Shaded', Icon: Circle },
  { id: 'facets', label: 'Facets', Icon: Triangle },
  { id: 'xray', label: 'X-ray', Icon: Scan },
];

/**
 * The floating viewport toolbar (bottom centre): HOW the part is rendered and
 * interacted with — render style, edges, lens overlay, section plane,
 * projection, fits and the measure tool. Orthogonal to the lens toolbar at
 * the top (WHAT is shown): nothing here resets the lens, and picking a lens
 * never resets these. Kept narrow so it cannot collide with the legend
 * (bottom-left) or the axis gizmo (bottom-right).
 */
export function ViewportToolbar() {
  const viewport = useV2((s) => s.viewport);
  const setViewport = useV2((s) => s.setViewport);

  return (
    <div className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-xl border border-zinc-950/10 bg-white/90 p-1 shadow-lg ring-1 ring-zinc-950/5 backdrop-blur dark:border-white/10 dark:bg-zinc-800/90 dark:ring-white/10">
      {STYLES.map(({ id, label, Icon }) => (
        <button
          key={id}
          type="button"
          disabled
          title={`${label} (coming with the render-layer refactor)`}
          aria-pressed={viewport.style === id}
          className={clsx(btnCls, disabledCls)}
        >
          <Icon className="size-4" />
        </button>
      ))}
      <Divider />

      <button type="button" disabled title="Edge display"
        className={clsx(btnCls, disabledCls)}>
        <Spline className="size-4" />
      </button>
      <button type="button" disabled title="Lens overlay"
        className={clsx(btnCls, disabledCls)}>
        <Layers className="size-4" />
      </button>
      <button type="button" disabled title="Section plane"
        className={clsx(btnCls, disabledCls)}>
        <Slice className="size-4" />
      </button>
      <Divider />

      <button
        type="button"
        onClick={() => setViewport({
          projection: viewport.projection === 'perspective'
            ? 'orthographic' : 'perspective',
        })}
        title={viewport.projection === 'perspective'
          ? 'Switch to orthographic projection'
          : 'Switch to perspective projection'}
        aria-pressed={viewport.projection === 'orthographic'}
        className={clsx(btnCls,
          viewport.projection === 'orthographic' ? activeCls : idleCls)}
      >
        <Cuboid className="size-4" />
      </button>
      <button
        type="button"
        onClick={fitPart}
        title="Fit part in view"
        className={clsx(btnCls, idleCls)}
      >
        <Maximize className="size-4" />
      </button>
      <Divider />

      <button type="button" disabled title="Measure"
        className={clsx(btnCls, disabledCls)}>
        <Ruler className="size-4" />
      </button>
    </div>
  );
}
