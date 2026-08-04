import clsx from 'clsx';
import { Crosshair, EyeOff, X } from 'lucide-react';
import { Button } from '../../catalyst/button';
import { Input } from '../../catalyst/input';
import type { SourceKind } from '../../processes/directions/build';
import { PROVENANCE_LABELS } from '../../processes/directions/modes';
import { provenanceCss } from '../../processes/directions/state';
import { useDirectionSetup } from '../../processes/directions/useSetup';
import { useStore } from '../../state/store';
import {
  Rail, RailBool, RailDivider, RailField, RailHeader, RailSection, RailStats,
} from '../components/rail';
import { VectorListField } from '../components/rail/fields/VectorListField';
import { hintCls } from '../components/styles';

const sameSet = (a: number[], b: number[]) =>
  a.length === b.length && a.every((x, i) => x === b[i]);

function Swatch({ source }: { source: SourceKind }) {
  return (
    <span
      className="size-3 shrink-0 rounded-full ring-1 ring-inset ring-zinc-950/10 dark:ring-white/20"
      style={{ backgroundColor: provenanceCss(source) }}
    />
  );
}

/**
 * The candidate-direction setup: a LIVE client-side set, not a param bag and
 * not a job — `useDirectionSetup` owns it and the arrows follow every edit.
 *
 * That is why this rail is not driven by `ViewMode.params` like a lens is: the
 * generated form edits `viewerParams`, and this edits a setup object with its
 * own patch semantics plus viewer pick state. The xyz editor it used to own IS
 * generic, though, and now lives in `components/rail/fields` as the default
 * widget for every `vector_list` param.
 */
export function DirectionsRail() {
  const manifest = useStore((s) => s.manifest);
  const stats = useStore((s) => s.stats);
  const { setup, patch, params, setParam } = useDirectionSetup();
  const setUi = (name: string, value: any) => setParam('directions', name, value);

  const picking = !!params.pickMode;
  const pendingBrep: number[] = params.pendingBrep ?? [];
  const highlightBrep: number[] = params.highlightBrep ?? [];
  const holeN = manifest?.hole_candidates?.length ?? 0;

  // provenance summary (setup-derived; the viewer stat shows the exact,
  // deduped arrow count)
  const summary = ([
    { src: 'uniform', n: setup.count },
    { src: 'principal_axis', n: setup.axes ? 6 : 0 },
    { src: 'bbox_axis', n: setup.bboxAxes ? 6 : 0 },
    { src: 'hole_axis', n: setup.holeAxes ? holeN * 2 : 0 },
    { src: 'manual', n: setup.manual.length },
    { src: 'average_normal', n: setup.brepGroups.length },
  ] as { src: SourceKind; n: number }[]).filter((s) => s.n > 0);

  function togglePick() {
    if (!picking) setUi('highlightBrep', []);
    setUi('pickMode', !picking);
    if (picking) setUi('pendingBrep', []);
  }
  function addGroup() {
    if (!pendingBrep.length) return;
    patch({ brepGroups: [...setup.brepGroups, [...pendingBrep].sort((a, b) => a - b)] });
    setUi('pendingBrep', []);
    setUi('pickMode', false);
  }
  function toggleHighlight(group: number[]) {
    setUi('highlightBrep', sameSet(highlightBrep, group) ? [] : group);
  }

  return (
    <Rail>
      <RailHeader
        icon={Crosshair}
        title="Candidate directions"
        blurb="The orientations to investigate. Arrows update live;
          accessibility is computed later when a check needs it."
      />

      {summary.length > 0 && (
        <RailSection title="Sources">
          <ul className="flex flex-col gap-1">
            {summary.map(({ src, n }) => (
              <li key={src} className="flex items-center gap-2 text-sm/5 text-zinc-700 dark:text-zinc-300">
                <Swatch source={src} />
                <span className="flex-1">{PROVENANCE_LABELS[src]}</span>
                <span className={hintCls}>{n}</span>
              </li>
            ))}
          </ul>
        </RailSection>
      )}

      <RailDivider />

      <RailField label="Uniform sample count">
        <Input
          type="number" min="0" step="1" value={String(setup.count)}
          aria-label="Uniform sample count"
          onChange={(e) => patch({ count: Math.max(0, parseInt(e.target.value) || 0) })}
        />
      </RailField>
      <RailBool label="World X / Y / Z axes" checked={setup.axes}
        onChange={(v) => patch({ axes: v })} />
      <RailBool label="Bounding-box (PCA) axes"
        hint="Part-aligned principal axes." checked={setup.bboxAxes}
        onChange={(v) => patch({ bboxAxes: v })} />
      <RailBool label={`Hole / cylinder axes${holeN ? ` (${holeN})` : ''}`}
        hint="Drill/bore axes from the analytic surfaces." checked={setup.holeAxes}
        onChange={(v) => patch({ holeAxes: v })} />

      <RailDivider />

      <RailField label="Manual axes">
        <VectorListField
          vectors={setup.manual}
          onChange={(manual) => patch({ manual })}
        />
      </RailField>

      <RailField
        label="Averaged normal from BREP faces"
        hint="Pick whole BREP faces in the viewer — their mean normal becomes
          one direction (for curved walls)."
      >
        <div className="flex items-center gap-2">
          {picking ? (
            <Button color="blue" onClick={togglePick}>
              <Crosshair data-slot="icon" /> Picking ({pendingBrep.length})
            </Button>
          ) : (
            <Button outline onClick={togglePick}>
              <Crosshair data-slot="icon" /> Pick faces
            </Button>
          )}
          <Button onClick={addGroup} disabled={!pendingBrep.length}>Add</Button>
        </div>
      </RailField>

      {setup.brepGroups.length > 0 && (
        <RailSection title="Averaged groups">
          <ul className="flex flex-col gap-1">
            {setup.brepGroups.map((g, i) => {
              const on = sameSet(highlightBrep, g);
              return (
                <li key={`g${i}`} className="flex items-center gap-2 text-sm/5 text-zinc-700 dark:text-zinc-300">
                  <Swatch source="average_normal" />
                  <button type="button"
                    className={clsx('flex-1 text-left hover:underline', on && 'font-medium text-blue-600 dark:text-blue-400')}
                    title="Highlight these BREP faces"
                    onClick={() => toggleHighlight(g)}>
                    {g.length === 1 ? `BREP face ${g[0]}` : `avg of ${g.length} BREP faces`}
                  </button>
                  <button type="button" aria-label="remove"
                    className="text-zinc-400 hover:text-red-600"
                    onClick={() => {
                      if (on) setUi('highlightBrep', []);
                      patch({ brepGroups: setup.brepGroups.filter((_, j) => j !== i) });
                    }}>
                    <X className="size-3.5" />
                  </button>
                </li>
              );
            })}
          </ul>
        </RailSection>
      )}

      {setup.suppressed.length > 0 && (
        <Button plain onClick={() => patch({ suppressed: [] })}>
          <EyeOff data-slot="icon" /> Restore {setup.suppressed.length} hidden
        </Button>
      )}

      {stats && <RailStats className="mt-auto" text={stats} />}
    </Rail>
  );
}
