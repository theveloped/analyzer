import clsx from 'clsx';
import { Ruler, X } from 'lucide-react';
import { useStore } from '../../state/store';
import { effectiveDescriptor, faceLabel } from '../../splits/splits';
import {
  computeMeasurement, type MeasureFrame, type MeasurePick,
} from '../../viewer/measure';
import { useV2 } from '../store';

const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';
const sectionCls = 'text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400';
const mm = (v: number) => `${v.toFixed(3)} mm`;
const deg = (v: number) => `${v.toFixed(1)}°`;

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-xs/5">
      <span className="text-zinc-500 dark:text-zinc-400">{label}</span>
      <span className="tabular-nums font-medium text-zinc-950 dark:text-white">{value}</span>
    </div>
  );
}

function PickBlock({ tag, pick }: { tag: 'A' | 'B'; pick: MeasurePick }) {
  const manifest = useStore((s) => s.manifest);
  const desc = manifest ? effectiveDescriptor(manifest) : undefined;
  const brep = pick.brepFace != null
    ? `BREP face ${faceLabel(pick.brepFace, desc)}` : 'no BREP id';
  return (
    <div className="rounded-lg bg-zinc-950/[.03] p-2 dark:bg-white/[.06]">
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-xs font-semibold text-zinc-950 dark:text-white">Point {tag}</span>
        <span className={hintCls}>face {pick.faceIndex} · {brep}</span>
      </div>
      <div className={`tabular-nums ${hintCls}`}>
        ({pick.point.map((v) => v.toFixed(3)).join(', ')})
      </div>
    </div>
  );
}

/**
 * The contextual rail while the Measure tool is active: pick provenance
 * (coordinates + face ids for auditability) and the derived readouts in
 * model units. The reported distance is the straight-line picked-point
 * distance — two mesh picks do NOT establish the minimum distance between
 * the complete BREP faces.
 */
const FRAMES: { id: MeasureFrame; label: string; hint: string }[] = [
  { id: 'xyz', label: 'XYZ', hint: 'Component legs along the model axes' },
  { id: 'normalA', label: 'Normal A', hint: "Along A's surface normal + in-plane rest" },
  { id: 'normalB', label: 'Normal B', hint: "Along B's surface normal + in-plane rest" },
];

export function MeasureRail() {
  const measure = useV2((s) => s.measure);
  const setMeasureActive = useV2((s) => s.setMeasureActive);
  const setMeasureFrame = useV2((s) => s.setMeasureFrame);
  const clearMeasurePicks = useV2((s) => s.clearMeasurePicks);
  const { a, b, frame } = measure;
  const readout = a && b ? computeMeasurement(a, b) : null;

  return (
    <div className="flex h-full w-72 shrink-0 flex-col gap-4 overflow-auto border-l border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900">
      <div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Ruler className="size-4 text-blue-600 dark:text-blue-400" />
            <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">Measure</h2>
          </div>
          <button
            type="button"
            onClick={() => setMeasureActive(false)}
            title="Exit measure (Esc)"
            className="rounded-lg p-1 text-zinc-400 transition hover:bg-zinc-950/5 hover:text-zinc-950 dark:hover:bg-white/10 dark:hover:text-white"
          >
            <X className="size-4" />
          </button>
        </div>
        <p className={`mt-1 ${hintCls}`}>
          {!a ? 'Click a first point on the part.'
            : !b ? 'Click a second point.'
            : 'A third click starts a new measurement.'}
        </p>
      </div>

      {a && <PickBlock tag="A" pick={a} />}
      {b && <PickBlock tag="B" pick={b} />}

      {readout && (
        <>
          <div>
            <div className={sectionCls}>Component frame</div>
            <div className="mt-1 flex gap-1">
              {FRAMES.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => setMeasureFrame(f.id)}
                  title={f.hint}
                  className={clsx(
                    'flex-1 rounded-lg px-1 py-1 text-xs font-medium transition',
                    frame === f.id
                      ? 'bg-zinc-900 text-white dark:bg-white dark:text-zinc-900'
                      : 'text-zinc-600 hover:bg-zinc-950/5 dark:text-zinc-300 dark:hover:bg-white/10',
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className={sectionCls}>Distance</div>
            <div className="mt-1 flex flex-col gap-1">
              <Row label="picked points |B−A|" value={mm(readout.distance)} />
              {frame === 'xyz' && (
                <>
                  <Row label="dX" value={mm(readout.delta[0])} />
                  <Row label="dY" value={mm(readout.delta[1])} />
                  <Row label="dZ" value={mm(readout.delta[2])} />
                </>
              )}
              {frame === 'normalA' && (
                <>
                  <Row label="along A's normal (signed)" value={mm(readout.alongNormalA)} />
                  <Row label="in A's plane" value={mm(readout.inPlane)} />
                </>
              )}
              {frame === 'normalB' && (
                <>
                  <Row label="along B's normal (signed)" value={mm(readout.alongNormalB)} />
                  <Row label="in B's plane" value={mm(readout.inPlaneB)} />
                </>
              )}
            </div>
            <p className={`mt-1.5 ${hintCls}`}>
              Straight-line distance between the picked points — not the
              minimum distance between the faces.
            </p>
          </div>

          <div>
            <div className={sectionCls}>Angles</div>
            <div className="mt-1 flex flex-col gap-1">
              <Row label="between normals (0–180°)" value={deg(readout.normalAngleDeg)} />
              <Row label="between planes (0–90°)" value={deg(readout.planeAngleDeg)} />
            </div>
          </div>
        </>
      )}

      {(a || b) && (
        <button
          type="button"
          onClick={clearMeasurePicks}
          className="rounded-lg border border-zinc-950/10 px-3 py-1.5 text-xs/5 font-medium text-zinc-700 transition hover:bg-zinc-950/5 dark:border-white/10 dark:text-zinc-300 dark:hover:bg-white/10"
        >
          Clear measurement
        </button>
      )}
    </div>
  );
}
