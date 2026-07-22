import clsx from 'clsx';
import {
  Boxes, Circle, Cuboid, Eye, Flag, Focus, Ghost, Layers, Maximize,
  RotateCcw, Ruler, Scan, Slice, Spline, Triangle,
} from 'lucide-react';
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
  { id: 'voxel', label: 'Voxel', hint: 'Part as voxel blocks (computes prep/voxels on first use)', Icon: Boxes },
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

/** Inline opacity control: the icon toggles 0 ↔ 100%, the slider dials any
 * value in between — no popover. */
function OpacityControl({ label, Icon, value, onChange }: {
  label: string;
  Icon: typeof Layers;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={() => onChange(value > 0 ? 0 : 1)}
        title={`${label} — click toggles, slider dials (${Math.round(value * 100)}%)`}
        aria-pressed={value > 0}
        className={clsx(btnCls, value > 0 ? activeCls : idleCls)}
      >
        <Icon className="size-4" />
      </button>
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        title={`${label} opacity (${Math.round(value * 100)}%)`}
        className="h-1 w-12 cursor-pointer accent-zinc-900 dark:accent-white"
      />
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

  const setContext = (mode: 'ghost' | 'isolate') =>
    setViewport({ context: viewport.context === mode ? 'all' : mode });

  return (
    // positioned in the free zone between the legend (bottom-left, 16rem) and
    // the axis gizmo (bottom-right, 128px + margin); wraps upward when tight.
    // On narrow columns the legend moves top-left, freeing the left edge.
    <div className="pointer-events-none absolute bottom-3 left-[16rem] right-[8.5rem] flex justify-center @max-2xl:left-3">
    <div className="pointer-events-auto flex max-w-full flex-wrap items-center justify-center gap-1 rounded-xl border border-zinc-950/10 bg-white/90 p-1 shadow-lg ring-1 ring-zinc-950/5 backdrop-blur dark:border-white/10 dark:bg-zinc-800/90 dark:ring-white/10">
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
      />
      <OpacityControl
        label="Findings"
        Icon={Flag}
        value={viewport.findingsOpacity}
        onChange={(findingsOpacity) => setViewport({ findingsOpacity })}
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
