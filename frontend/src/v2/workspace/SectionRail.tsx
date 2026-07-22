import clsx from 'clsx';
import { Crosshair, Slice, X } from 'lucide-react';
import { partBounds, viewDirection } from '../../viewer/controller';
import {
  DEFAULT_SECTION, type SectionState,
} from '../../viewer/viewportState';
import { armSectionSnap } from '../tools/sectionSnap';
import { useV2 } from '../store';

const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';
const sectionCls = 'text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400';
const segActive = 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900';
const segIdle = 'text-zinc-600 hover:bg-zinc-950/5 dark:text-zinc-300 dark:hover:bg-white/10';

const AXIS_NORMALS: Record<'x' | 'y' | 'z', [number, number, number]> = {
  x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1],
};

/** Offset range of the part bbox along a normal (projected corners). */
function offsetRange(normal: [number, number, number]): [number, number] {
  const bounds = partBounds();
  if (!bounds) return [-100, 100];
  let lo = Infinity;
  let hi = -Infinity;
  for (const x of [bounds.min[0], bounds.max[0]]) {
    for (const y of [bounds.min[1], bounds.max[1]]) {
      for (const z of [bounds.min[2], bounds.max[2]]) {
        const d = x * normal[0] + y * normal[1] + z * normal[2];
        if (d < lo) lo = d;
        if (d > hi) hi = d;
      }
    }
  }
  return lo <= hi ? [lo, hi] : [-100, 100];
}

/**
 * The right rail for the section plane (opened from the viewport toolbar,
 * like the measure rail): axis or view-seeded orientation, offset slider +
 * numeric value, snap-to-picked-geometry, flip and reset. Closing the rail
 * leaves the section itself untouched — it is viewport state.
 */
export function SectionRail() {
  const section = useV2((s) => s.viewport.section);
  const setViewport = useV2((s) => s.setViewport);
  const setSectionRailOpen = useV2((s) => s.setSectionRailOpen);
  const patch = (p: Partial<SectionState>) =>
    setViewport({ section: { ...section, ...p } });
  const [lo, hi] = offsetRange(section.normal);
  const mid = (lo + hi) / 2;
  const span = Math.max(hi - lo, 1e-6);

  const pickAxis = (axis: 'x' | 'y' | 'z') => {
    const normal = AXIS_NORMALS[axis];
    const [alo, ahi] = offsetRange(normal);
    patch({
      enabled: true, axis, normal,
      offset: section.enabled && section.axis === axis
        ? section.offset : (alo + ahi) / 2,
    });
  };
  const pickView = () => {
    const normal = viewDirection();
    const [alo, ahi] = offsetRange(normal);
    patch({ enabled: true, axis: 'custom', normal, offset: (alo + ahi) / 2 });
  };

  return (
    <div className="flex h-full w-72 shrink-0 flex-col gap-4 overflow-auto border-l border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900">
      <div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Slice className="size-4 text-blue-600 dark:text-blue-400" />
            <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">Section</h2>
          </div>
          <button
            type="button"
            onClick={() => setSectionRailOpen(false)}
            title="Close (the section itself stays as set)"
            className="rounded-lg p-1 text-zinc-400 transition hover:bg-zinc-950/5 hover:text-zinc-950 dark:hover:bg-white/10 dark:hover:text-white"
          >
            <X className="size-4" />
          </button>
        </div>
        <p className={`mt-1 ${hintCls}`}>
          One plane cutting every layer. Watertight parts get a solid cap on
          the cut face.
        </p>
      </div>

      <div>
        <div className={sectionCls}>Orientation</div>
        <div className="mt-1 flex gap-1">
          {(['x', 'y', 'z'] as const).map((axis) => (
            <button
              key={axis}
              type="button"
              onClick={() => pickAxis(axis)}
              className={clsx('flex-1 rounded-lg px-1 py-1.5 text-xs font-medium uppercase transition',
                section.enabled && section.axis === axis ? segActive : segIdle)}
            >
              {axis}
            </button>
          ))}
          <button
            type="button"
            onClick={pickView}
            title="Plane facing the current view"
            className={clsx('flex-1 rounded-lg px-1 py-1.5 text-xs font-medium transition',
              section.enabled && section.axis === 'custom' ? segActive : segIdle)}
          >
            View
          </button>
        </div>
        <button
          type="button"
          onClick={armSectionSnap}
          title="Click a face next: snap to its plane / centerline / vertex"
          className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg border border-zinc-950/10 px-2 py-1.5 text-xs font-medium text-zinc-700 transition hover:bg-zinc-950/5 dark:border-white/10 dark:text-zinc-300 dark:hover:bg-white/10"
        >
          <Crosshair className="size-3.5" /> Pick target on the part
        </button>
      </div>

      <div>
        <div className={sectionCls}>Offset</div>
        <input
          type="range"
          min={lo}
          max={hi}
          step={span / 200}
          disabled={!section.enabled}
          value={section.enabled ? section.offset : mid}
          onChange={(e) => patch({ offset: parseFloat(e.target.value) })}
          className="mt-1 w-full"
          title="Section offset"
        />
        <div className="mt-1 flex items-center gap-2">
          <input
            type="number"
            disabled={!section.enabled}
            value={section.enabled ? Number(section.offset.toFixed(2)) : ''}
            step={Number((span / 100).toPrecision(2))}
            onChange={(e) => {
              const v = parseFloat(e.target.value);
              if (isFinite(v)) patch({ offset: v });
            }}
            className="w-24 rounded-lg border border-zinc-950/10 bg-transparent px-2 py-1 text-xs tabular-nums text-zinc-700 dark:border-white/10 dark:text-zinc-300"
          />
          <span className="text-xs text-zinc-400">mm</span>
          <span className="flex-1" />
          <button
            type="button"
            disabled={!section.enabled}
            onClick={() => patch({ flip: !section.flip })}
            className={clsx('rounded-lg px-2.5 py-1 text-xs font-medium transition',
              section.flip ? segActive : segIdle)}
          >
            Flip
          </button>
          <button
            type="button"
            onClick={() => setViewport({ section: DEFAULT_SECTION })}
            className={clsx('rounded-lg px-2.5 py-1 text-xs font-medium transition', segIdle)}
          >
            Reset
          </button>
        </div>
      </div>
    </div>
  );
}
