import clsx from 'clsx';
import {
  Boxes, Circle, Cuboid, Eye, Flag, Focus, Ghost, Layers, Maximize,
  RotateCcw, Ruler, Scan, Slice, Spline, Triangle,
} from 'lucide-react';
import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { useStore } from '../../state/store';
import { fitPart, fitSelection, selectLegendGroup } from '../../viewer/controller';
import { DEFAULT_VIEWPORT, type RenderStyle } from '../../viewer/viewportState';
import { edgeDescriptors } from '../../splits/splits';
import { useV2 } from '../store';

const btnCls = 'flex size-8 items-center justify-center rounded-lg transition';
const activeCls = 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900';
const idleCls = 'text-zinc-500 hover:bg-zinc-950/5 hover:text-zinc-950 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-white';

function Divider() {
  return <span className="mx-0.5 h-5 w-px bg-zinc-950/10 dark:bg-white/10" />;
}

const STYLES: { id: RenderStyle; label: string; hint: string; Icon: typeof Circle }[] = [
  { id: 'solid', label: 'Solid', hint: 'Smooth solid shading', Icon: Circle },
  { id: 'mesh', label: 'Mesh', hint: 'Flat triangle shading with tessellation edges', Icon: Triangle },
  { id: 'xray', label: 'X-ray', hint: 'See-through shell, occluded findings stay visible', Icon: Scan },
  { id: 'voxel', label: 'Voxel', hint: 'Voxel field coloured by wall distance, internal cells visible (computes prep/voxels on first use)', Icon: Boxes },
];

/** Plain toggle: show/hide the true BREP boundary polylines. */
function EdgeToggle() {
  const brepEdges = useV2((s) => s.viewport.brepEdges);
  const setViewport = useV2((s) => s.setViewport);
  const manifest = useStore((s) => s.manifest);
  const hasBrep = !!manifest && !!edgeDescriptors(manifest);
  return (
    <button
      type="button"
      disabled={!hasBrep}
      onClick={() => setViewport({ brepEdges: !brepEdges })}
      title={hasBrep ? 'Show BREP boundary edges'
        : 'BREP edges — no boundary data for this part (STL input)'}
      aria-pressed={brepEdges}
      className={clsx(btnCls, !hasBrep ? 'cursor-not-allowed text-zinc-300 dark:text-zinc-600'
        : brepEdges ? activeCls : idleCls)}
    >
      <Spline className="size-4" />
    </button>
  );
}

/** How much an open opacity control widens the ribbon: the toolbar pill
 * compensates its right margin by the same amount so the pill's LEFT edge
 * (and the hovered icon) stay put under the centring wrapper — expansion
 * pushes the sibling icons right, never the icon itself left. */
export const OPACITY_FLYOUT_PX = 60;

/** YouTube-volume-style opacity control: the icon alone sits in the ribbon;
 * clicking it mutes to 0% or restores the last dialled value (100% until
 * changed). Hovering — or keyboard-focusing — flows a slider out of the
 * icon, pushing the neighbouring icons right; icon and slider share one
 * backdrop chip in the icon's state colour (dark when on, grey when muted).
 * It stays out while dragging, even when the pointer strays mid-drag. */
function OpacityControl({ label, Icon, value, onChange, onOpenChange }: {
  label: string;
  Icon: typeof Layers;
  value: number;
  onChange: (value: number) => void;
  onOpenChange: (open: boolean) => void;
}) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dragging, setDragging] = useState(false);
  // what "unmute" restores — the last non-zero value the user dialled
  const restore = useRef(1);
  if (value > 0) restore.current = value;
  const open = hovered || focused || dragging;
  // LAYOUT effect: the pill's compensating margin must land in the same
  // paint as the width change — one frame of mismatch shifts the pill
  // under the pointer, breaking hover and oscillating the control
  useLayoutEffect(() => {
    if (!open) return;
    onOpenChange(true);
    return () => onOpenChange(false);
  }, [open, onOpenChange]);
  const on = value > 0;

  return (
    <div
      // the container IS the shared backdrop: an elongated version of the
      // icon's own chip (active = dark, muted = the grey hover tint)
      className={clsx('flex items-center rounded-lg transition-colors duration-200',
        open && (on ? 'bg-zinc-900 dark:bg-white' : 'bg-zinc-950/5 dark:bg-white/10'))}
      onPointerEnter={() => setHovered(true)}
      onPointerLeave={() => setHovered(false)}
      // hold the slider out for KEYBOARD focus only — a mouse click also
      // focuses the input, which must not pin the slider open after leaving
      onFocus={(e) => setFocused(e.target.matches(':focus-visible'))}
      onBlur={() => setFocused(false)}
    >
      <button
        type="button"
        onClick={() => onChange(on ? 0 : restore.current)}
        title={`${label} ${Math.round(value * 100)}% — click toggles, hover to dial`}
        aria-pressed={on}
        className={clsx(btnCls,
          on ? (open ? 'text-white dark:text-zinc-900' : activeCls) : idleCls)}
      >
        <Icon className="size-4" />
      </button>
      {/* in-flow slider zone (w = OPACITY_FLYOUT_PX when open) */}
      <div
        className={clsx(
          'flex items-center overflow-hidden transition-[width] duration-200 ease-out',
          open ? 'w-[60px]' : 'w-0',
        )}
      >
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          onPointerDown={() => setDragging(true)}
          onPointerUp={() => setDragging(false)}
          onPointerCancel={() => setDragging(false)}
          tabIndex={open ? 0 : -1}
          title={`${label} opacity (${Math.round(value * 100)}%)`}
          // Catalyst-style track + ball thumb, inverted to read on the
          // state-coloured backdrop
          className={clsx(
            'ml-1 mr-1 h-1 w-12 shrink-0 cursor-pointer appearance-none rounded-full',
            on
              ? '[--fill:#ffffff] [--track:#ffffff4d] dark:[--fill:#18181b] dark:[--track:#00000033]'
              : '[--fill:#18181b] [--track:#d4d4d8] dark:[--fill:#ffffff] dark:[--track:#ffffff33]',
            '[&::-webkit-slider-thumb]:size-3 [&::-webkit-slider-thumb]:appearance-none',
            '[&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white',
            '[&::-webkit-slider-thumb]:shadow [&::-webkit-slider-thumb]:ring-1',
            '[&::-webkit-slider-thumb]:ring-zinc-950/20',
            '[&::-moz-range-thumb]:size-3 [&::-moz-range-thumb]:rounded-full',
            '[&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:bg-white',
            '[&::-moz-range-thumb]:shadow',
          )}
          style={{
            background: `linear-gradient(to right, var(--fill) ${value * 100}%, var(--track) ${value * 100}%)`,
          }}
        />
      </div>
    </div>
  );
}

/**
 * The floating viewport toolbar (bottom centre): HOW the part is rendered and
 * interacted with — render style, edges, the lens/findings overlay opacities
 * (inline), section rail, projection, fits and the measure tool. Orthogonal
 * to the lens toolbar at the top (WHAT is shown): nothing here resets the
 * lens, and picking a lens never resets these. Kept clear of the legend
 * (bottom-left) and the axis gizmo (bottom-right); wraps upward when tight.
 */
export function ViewportToolbar() {
  const viewport = useV2((s) => s.viewport);
  const setViewport = useV2((s) => s.setViewport);
  const measuring = useV2((s) => s.measure.active);
  const setMeasureActive = useV2((s) => s.setMeasureActive);
  const sectionRailOpen = useV2((s) => s.sectionRailOpen);
  const setSectionRailOpen = useV2((s) => s.setSectionRailOpen);
  const selection = useStore((s) => s.selection);
  // opacity fly-outs widen the pill; a matching negative right margin keeps
  // its layout width constant so the centring wrapper never re-centres —
  // the hovered icon stays put and only the icons to its right shift
  const [openFlyouts, setOpenFlyouts] = useState(0);
  const onFlyoutChange = useCallback((open: boolean) =>
    setOpenFlyouts((n) => n + (open ? 1 : -1)), []);

  const setContext = (mode: 'ghost' | 'isolate') =>
    setViewport({ context: viewport.context === mode ? 'all' : mode });

  return (
    // positioned in the free zone between the legend (bottom-left, 16rem) and
    // the axis gizmo (bottom-right, 128px + margin); wraps upward when tight.
    // On narrow columns the legend moves top-left, freeing the left edge.
    <div className="pointer-events-none absolute bottom-3 left-[16rem] right-[8.5rem] flex justify-center @max-2xl:left-3">
    <div
      className="pointer-events-auto flex flex-wrap items-center justify-center gap-1 rounded-xl border border-zinc-950/10 bg-white/90 p-1 shadow-lg ring-1 ring-zinc-950/5 backdrop-blur transition-[margin] duration-200 ease-out dark:border-white/10 dark:bg-zinc-800/90 dark:ring-white/10"
      // a fly-out widens the content by OPACITY_FLYOUT_PX: the negative
      // margin keeps the centring wrapper from re-centring (icon stays put)
      // and the raised max-width keeps flex-wrap from RE-PACKING the rows —
      // a re-wrap moves the hovered icon and oscillates the hover state
      style={{
        marginRight: -openFlyouts * OPACITY_FLYOUT_PX,
        maxWidth: `calc(100% + ${openFlyouts * OPACITY_FLYOUT_PX}px)`,
      }}
    >
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

      <EdgeToggle />
      <OpacityControl
        label="Lens colours"
        Icon={Layers}
        value={viewport.lensOpacity}
        onChange={(lensOpacity) => setViewport({ lensOpacity })}
        onOpenChange={onFlyoutChange}
      />
      <OpacityControl
        label="Findings"
        Icon={Flag}
        value={viewport.findingsOpacity}
        onChange={(findingsOpacity) => setViewport({ findingsOpacity })}
        onOpenChange={onFlyoutChange}
      />
      <button
        type="button"
        onClick={() => setSectionRailOpen(!sectionRailOpen)}
        title="Section plane (opens the section rail)"
        aria-pressed={sectionRailOpen || viewport.section.enabled}
        className={clsx(btnCls,
          sectionRailOpen || viewport.section.enabled ? activeCls : idleCls)}
      >
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
      <button
        type="button"
        onClick={() => {
          useV2.setState({ viewport: DEFAULT_VIEWPORT });
          selectLegendGroup('', null);
        }}
        title="Reset viewport (style, edges, opacities, section, projection)"
        className={clsx(btnCls, idleCls)}
      >
        <RotateCcw className="size-4" />
      </button>

      {/* selection context — appears once a legend row selected a group */}
      {selection && (
        <>
          <Divider />
          <button
            type="button"
            onClick={fitSelection}
            title={`Fit selection (${selection.label})`}
            className={clsx(btnCls, idleCls)}
          >
            <Focus className="size-4" />
          </button>
          <button
            type="button"
            onClick={() => setContext('ghost')}
            title={`Ghost everything but the selection (${selection.label})`}
            aria-pressed={viewport.context === 'ghost'}
            className={clsx(btnCls, viewport.context === 'ghost' ? activeCls : idleCls)}
          >
            <Ghost className="size-4" />
          </button>
          <button
            type="button"
            onClick={() => setContext('isolate')}
            title={`Isolate the selection (${selection.label})`}
            aria-pressed={viewport.context === 'isolate'}
            className={clsx(btnCls, viewport.context === 'isolate' ? activeCls : idleCls)}
          >
            <Eye className="size-4" />
          </button>
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
        </>
      )}
      <Divider />

      {/* interaction tool, not a render setting: owns mesh clicks while on */}
      <button
        type="button"
        onClick={() => setMeasureActive(!measuring)}
        title={measuring ? 'Exit measure (Esc)' : 'Measure two points'}
        aria-pressed={measuring}
        className={clsx(btnCls, measuring ? activeCls : idleCls)}
      >
        <Ruler className="size-4" />
      </button>
    </div>
    </div>
  );
}
