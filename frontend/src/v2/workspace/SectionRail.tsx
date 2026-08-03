import { Crosshair, Slice } from 'lucide-react';
import { Button } from '../../catalyst/button';
import { Input } from '../../catalyst/input';
import { partBounds, viewDirection } from '../../viewer/controller';
import {
  DEFAULT_SECTION, type SectionState,
} from '../../viewer/viewportState';
import {
  Rail, RailHeader, RailSection, RailSegmented,
} from '../components/rail';
import { useV2 } from '../store';
import { armSectionSnap } from '../tools/sectionSnap';

const AXIS_NORMALS: Record<'x' | 'y' | 'z', [number, number, number]> = {
  x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1],
};

const ORIENTATIONS = [
  { id: 'x', label: 'X' },
  { id: 'y', label: 'Y' },
  { id: 'z', label: 'Z' },
  { id: 'view', label: 'View', title: 'Plane facing the current view' },
] as const;

type Orientation = (typeof ORIENTATIONS)[number]['id'];

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

  // no orientation is selected until the section is on, so an off section
  // shows an empty group rather than a lie about which plane is active
  const orientation: Orientation | '' = !section.enabled ? ''
    : section.axis === 'custom' ? 'view' : section.axis;

  return (
    <Rail>
      <RailHeader
        icon={Slice}
        title="Section"
        onClose={() => setSectionRailOpen(false)}
        closeTitle="Close (the section itself stays as set)"
        blurb="One plane cutting every layer. Watertight parts get a solid cap
          on the cut face."
      />

      <RailSection title="Orientation" variant="micro">
        <RailSegmented
          options={ORIENTATIONS as unknown as { id: Orientation; label: string; title?: string }[]}
          value={orientation as Orientation}
          onChange={(id) => (id === 'view' ? pickView() : pickAxis(id))}
          ariaLabel="Section orientation"
        />
        <Button
          outline
          onClick={armSectionSnap}
          title="Click a face next: snap to its plane / centerline / vertex"
          className="mt-2 w-full"
        >
          <Crosshair data-slot="icon" /> Pick target on the part
        </Button>
      </RailSection>

      <RailSection title="Offset" variant="micro">
        <input
          type="range"
          min={lo}
          max={hi}
          step={span / 200}
          disabled={!section.enabled}
          value={section.enabled ? section.offset : mid}
          onChange={(e) => patch({ offset: parseFloat(e.target.value) })}
          className="w-full"
          title="Section offset"
        />
        <div className="mt-1 flex items-center gap-2">
          <div className="w-24 shrink-0">
            <Input
              type="number"
              disabled={!section.enabled}
              value={section.enabled ? String(Number(section.offset.toFixed(2))) : ''}
              step={Number((span / 100).toPrecision(2))}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                if (isFinite(v)) patch({ offset: v });
              }}
              aria-label="Section offset value"
            />
          </div>
          <span className="text-xs text-zinc-400">mm</span>
          <span className="flex-1" />
          {/* Catalyst discriminates its variants by literal props, so a
              pressed toggle is two elements rather than one with a boolean */}
          {section.flip ? (
            <Button aria-pressed disabled={!section.enabled}
              onClick={() => patch({ flip: false })}>
              Flip
            </Button>
          ) : (
            <Button outline aria-pressed={false} disabled={!section.enabled}
              onClick={() => patch({ flip: true })}>
              Flip
            </Button>
          )}
          <Button plain onClick={() => setViewport({ section: DEFAULT_SECTION })}>
            Reset
          </Button>
        </div>
      </RailSection>
    </Rail>
  );
}
