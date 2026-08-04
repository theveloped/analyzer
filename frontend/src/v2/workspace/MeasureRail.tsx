import { Ruler } from 'lucide-react';
import { Button } from '../../catalyst/button';
import { useStore } from '../../state/store';
import { effectiveDescriptor, faceLabel } from '../../splits/splits';
import {
  computeMeasurement, type MeasureFrame, type MeasurePick,
} from '../../viewer/measure';
import {
  Rail, RailHeader, RailSection, RailSegmented,
} from '../components/rail';
import { hintCls } from '../components/styles';
import { useV2 } from '../store';

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
 *
 * An instrument rather than a settings panel, so its sections use the `micro`
 * heading. It is otherwise the same skeleton as every other rail — the two
 * tool rails used to share a private dialect with each other and with nothing
 * else.
 */
const FRAMES: { id: MeasureFrame; label: string; title: string }[] = [
  { id: 'xyz', label: 'XYZ', title: 'Component legs along the model axes' },
  { id: 'normalA', label: 'Normal A', title: "Along A's surface normal + in-plane rest" },
  { id: 'normalB', label: 'Normal B', title: "Along B's surface normal + in-plane rest" },
];

export function MeasureRail() {
  const measure = useV2((s) => s.measure);
  const setMeasureActive = useV2((s) => s.setMeasureActive);
  const setMeasureFrame = useV2((s) => s.setMeasureFrame);
  const clearMeasurePicks = useV2((s) => s.clearMeasurePicks);
  const { a, b, frame } = measure;
  const readout = a && b ? computeMeasurement(a, b) : null;

  return (
    <Rail>
      <RailHeader
        icon={Ruler}
        title="Measure"
        onClose={() => setMeasureActive(false)}
        closeTitle="Exit measure (Esc)"
        blurb={!a ? 'Click a first point on the part.'
          : !b ? 'Click a second point.'
            : 'A third click starts a new measurement.'}
      />

      {a && <PickBlock tag="A" pick={a} />}
      {b && <PickBlock tag="B" pick={b} />}

      {readout && (
        <>
          <RailSection title="Component frame" variant="micro">
            <RailSegmented
              options={FRAMES}
              value={frame}
              onChange={setMeasureFrame}
              ariaLabel="Component frame"
            />
          </RailSection>

          <RailSection title="Distance" variant="micro">
            <div className="flex flex-col gap-1">
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
          </RailSection>

          <RailSection title="Angles" variant="micro">
            <div className="flex flex-col gap-1">
              <Row label="between normals (0–180°)" value={deg(readout.normalAngleDeg)} />
              <Row label="between planes (0–90°)" value={deg(readout.planeAngleDeg)} />
            </div>
          </RailSection>
        </>
      )}

      {(a || b) && (
        <Button outline onClick={clearMeasurePicks}>Clear measurement</Button>
      )}
    </Rail>
  );
}
