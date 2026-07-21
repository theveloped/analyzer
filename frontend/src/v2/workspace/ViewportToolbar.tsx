import { Popover, PopoverButton, PopoverPanel } from '@headlessui/react';
import clsx from 'clsx';
import {
  Circle, Cuboid, Eye, Focus, Ghost, Layers, Maximize, Ruler, Scan, Slice,
  Spline, Triangle,
} from 'lucide-react';
import { useStore } from '../../state/store';
import { fitPart, fitSelection, selectLegendGroup } from '../../viewer/controller';
import type { EdgeMode, RenderStyle } from '../../viewer/viewportState';
import { edgeDescriptors } from '../../splits/splits';
import { useV2 } from '../store';

const btnCls = 'flex size-8 items-center justify-center rounded-lg transition';
const activeCls = 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900';
const idleCls = 'text-zinc-500 hover:bg-zinc-950/5 hover:text-zinc-950 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-white';
const disabledCls = 'cursor-not-allowed text-zinc-300 dark:text-zinc-600';
const panelCls = 'z-20 mb-2 w-56 rounded-xl border border-zinc-950/10 bg-white/95 p-2 shadow-lg ring-1 ring-zinc-950/5 backdrop-blur dark:border-white/10 dark:bg-zinc-800/95 dark:ring-white/10';
const rowCls = 'flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm/5 transition';
const rowActiveCls = 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900';
const rowIdleCls = 'text-zinc-700 hover:bg-zinc-950/5 dark:text-zinc-300 dark:hover:bg-white/10';
const labelCls = 'px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-zinc-400';

function Divider() {
  return <span className="mx-0.5 h-5 w-px bg-zinc-950/10 dark:bg-white/10" />;
}

const STYLES: { id: RenderStyle; label: string; hint: string; Icon: typeof Circle }[] = [
  { id: 'shaded', label: 'Shaded', hint: 'Smooth solid shading', Icon: Circle },
  { id: 'facets', label: 'Facets', hint: 'Flat triangle shading with edges', Icon: Triangle },
  { id: 'xray', label: 'X-ray', hint: 'See-through shell, occluded findings stay visible', Icon: Scan },
];

/** Grouped, searchable menu-style popover for the edge display mode. */
function EdgeMenu() {
  const viewport = useV2((s) => s.viewport);
  const setViewport = useV2((s) => s.setViewport);
  const manifest = useStore((s) => s.manifest);
  const hasBrep = !!manifest && !!edgeDescriptors(manifest);
  const options: { id: EdgeMode; label: string; disabled?: boolean }[] = [
    { id: 'none', label: 'No edges' },
    { id: 'brep', label: 'BREP boundaries', disabled: !hasBrep },
    { id: 'tessellation', label: 'Tessellation triangles' },
  ];
  return (
    <Popover className="relative">
      <PopoverButton
        title="Edge display"
        aria-label="Edge display"
        className={clsx(btnCls, viewport.edgeMode !== 'none' ? activeCls : idleCls)}
      >
        <Spline className="size-4" />
      </PopoverButton>
      <PopoverPanel anchor="top" className={panelCls}>
        <div className={labelCls}>Edges</div>
        {options.map((option) => (
          <PopoverButton
            as="button"
            key={option.id}
            type="button"
            disabled={option.disabled}
            title={option.disabled ? 'no BREP boundary data for this part (STL input)' : undefined}
            onClick={() => setViewport({ edgeMode: option.id })}
            className={clsx(rowCls,
              viewport.edgeMode === option.id ? rowActiveCls
                : option.disabled ? 'text-zinc-300 dark:text-zinc-600' : rowIdleCls)}
          >
            {option.label}
          </PopoverButton>
        ))}
      </PopoverPanel>
    </Popover>
  );
}

/** Lens overlay visibility, opacity and the findings-only filter. */
function OverlayMenu() {
  const viewport = useV2((s) => s.viewport);
  const setViewport = useV2((s) => s.setViewport);
  const dimmed = !viewport.lensVisible || viewport.lensOpacity < 1
    || viewport.findingsOnly;
  return (
    <Popover className="relative">
      <PopoverButton
        title="Lens overlay"
        aria-label="Lens overlay"
        className={clsx(btnCls, dimmed ? activeCls : idleCls)}
      >
        <Layers className="size-4" />
      </PopoverButton>
      <PopoverPanel anchor="top" className={panelCls}>
        <div className={labelCls}>Lens overlay</div>
        <label className={clsx(rowCls, rowIdleCls, 'cursor-pointer')}>
          <input
            type="checkbox"
            checked={viewport.lensVisible}
            onChange={(e) => setViewport({ lensVisible: e.target.checked })}
            className="size-3.5 rounded border-zinc-400"
          />
          Show lens colours
        </label>
        <label className={clsx(rowCls, rowIdleCls, 'cursor-pointer')}>
          <input
            type="checkbox"
            checked={viewport.findingsOnly}
            onChange={(e) => setViewport({ findingsOnly: e.target.checked })}
            className="size-3.5 rounded border-zinc-400"
          />
          Findings only
        </label>
        <div className={clsx(rowCls, 'text-zinc-700 dark:text-zinc-300')}>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={viewport.lensOpacity}
            onChange={(e) => setViewport({ lensOpacity: parseFloat(e.target.value) })}
            className="w-full"
            title="Lens opacity"
          />
          <span className="w-9 text-right text-xs tabular-nums">
            {Math.round(viewport.lensOpacity * 100)}%
          </span>
        </div>
      </PopoverPanel>
    </Popover>
  );
}

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
  const selection = useStore((s) => s.selection);

  const setContext = (mode: 'ghost' | 'isolate') =>
    setViewport({ context: viewport.context === mode ? 'all' : mode });

  return (
    <div className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-xl border border-zinc-950/10 bg-white/90 p-1 shadow-lg ring-1 ring-zinc-950/5 backdrop-blur dark:border-white/10 dark:bg-zinc-800/90 dark:ring-white/10">
      {STYLES.map(({ id, label, hint, Icon }) => (
        <button
          key={id}
          type="button"
          onClick={() => setViewport({ style: id })}
          title={`${label} — ${hint}`}
          aria-pressed={viewport.style === id}
          className={clsx(btnCls, viewport.style === id ? activeCls : idleCls)}
        >
          <Icon className="size-4" />
        </button>
      ))}
      <Divider />

      <EdgeMenu />
      <OverlayMenu />
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

      {/* selection context — select a group by clicking a legend row */}
      <button
        type="button"
        disabled={!selection}
        onClick={fitSelection}
        title={selection ? `Fit selection (${selection.label})`
          : 'Fit selection — click a legend entry to select its faces'}
        className={clsx(btnCls, selection ? idleCls : disabledCls)}
      >
        <Focus className="size-4" />
      </button>
      <button
        type="button"
        disabled={!selection}
        onClick={() => setContext('ghost')}
        title={selection ? `Ghost everything but the selection (${selection.label})`
          : 'Ghost context — click a legend entry to select its faces'}
        aria-pressed={viewport.context === 'ghost'}
        className={clsx(btnCls,
          !selection ? disabledCls : viewport.context === 'ghost' ? activeCls : idleCls)}
      >
        <Ghost className="size-4" />
      </button>
      <button
        type="button"
        disabled={!selection}
        onClick={() => setContext('isolate')}
        title={selection ? `Isolate the selection (${selection.label})`
          : 'Isolate selection — click a legend entry to select its faces'}
        aria-pressed={viewport.context === 'isolate'}
        className={clsx(btnCls,
          !selection ? disabledCls : viewport.context === 'isolate' ? activeCls : idleCls)}
      >
        <Eye className="size-4" />
      </button>
      {selection && (
        <button
          type="button"
          onClick={() => {
            selectLegendGroup('', null);
            setViewport({ context: 'all' });
          }}
          title={`Clear selection (${selection.label})`}
          className="rounded-lg px-1.5 text-[10px] font-medium text-zinc-400 transition hover:bg-zinc-950/5 hover:text-zinc-950 dark:hover:bg-white/10 dark:hover:text-white"
        >
          ✕
        </button>
      )}
      <Divider />

      <button type="button" disabled title="Measure"
        className={clsx(btnCls, disabledCls)}>
        <Ruler className="size-4" />
      </button>
    </div>
  );
}
