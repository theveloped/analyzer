import clsx from 'clsx';
import { Crosshair, EyeOff, Plus, X } from 'lucide-react';
import { useState } from 'react';
import { Button } from '../../catalyst/button';
import { Input } from '../../catalyst/input';
import { Switch } from '../../catalyst/switch';
import type { SourceKind } from '../../processes/directions/build';
import { PROVENANCE_LABELS } from '../../processes/directions/modes';
import { provenanceCss } from '../../processes/directions/state';
import { useDirectionSetup } from '../../processes/directions/useSetup';
import { useStore } from '../../state/store';

const labelCls = 'text-sm/6 font-medium text-zinc-950 dark:text-white';
const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';
const num = (v: any) => { const n = parseFloat(v); return isFinite(n) ? n : NaN; };
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

function BoolRow({ label, hint, checked, onChange }: {
  label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <div className={labelCls}>{label}</div>
        {hint && <p className={hintCls}>{hint}</p>}
      </div>
      <Switch checked={checked} onChange={onChange} aria-label={label} />
    </div>
  );
}

export function DirectionsRail() {
  const manifest = useStore((s) => s.manifest);
  const stats = useStore((s) => s.stats);
  const { setup, patch, params, setParam } = useDirectionSetup();
  const setUi = (name: string, value: any) => setParam('directions', name, value);

  const [ax, setAx] = useState('0');
  const [ay, setAy] = useState('0');
  const [az, setAz] = useState('1');

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

  function addAxis() {
    const v = [num(ax), num(ay), num(az)];
    if (v.some((c) => !isFinite(c)) || v.every((c) => c === 0)) return;
    patch({ manual: [...setup.manual, v] });
  }
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

  const fmt = (v: number[]) => v.map((c) => (+c).toFixed(2)).join(', ');

  return (
    <div className="flex h-full w-72 shrink-0 flex-col gap-4 overflow-auto border-l border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900">
      <div>
        <div className="flex items-center gap-2">
          <Crosshair className="size-4 text-blue-600 dark:text-blue-400" />
          <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">Candidate directions</h2>
        </div>
        <p className={clsx('mt-1', hintCls)}>
          The orientations to investigate. Arrows update live; accessibility is computed later when a check needs it.
        </p>
      </div>

      {summary.length > 0 && (
        <div>
          <div className="mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">Sources</div>
          <ul className="flex flex-col gap-1">
            {summary.map(({ src, n }) => (
              <li key={src} className="flex items-center gap-2 text-sm/5 text-zinc-700 dark:text-zinc-300">
                <Swatch source={src} />
                <span className="flex-1">{PROVENANCE_LABELS[src]}</span>
                <span className={hintCls}>{n}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="h-px bg-zinc-950/10 dark:bg-white/10" />

      <div>
        <label className={labelCls}>Uniform sample count</label>
        <div className="mt-2">
          <Input
            type="number" min="0" step="1" value={String(setup.count)}
            onChange={(e) => patch({ count: Math.max(0, parseInt(e.target.value) || 0) })}
          />
        </div>
      </div>
      <BoolRow label="World X / Y / Z axes" checked={setup.axes}
        onChange={(v) => patch({ axes: v })} />
      <BoolRow label="Bounding-box (PCA) axes"
        hint="Part-aligned principal axes." checked={setup.bboxAxes}
        onChange={(v) => patch({ bboxAxes: v })} />
      <BoolRow label={`Hole / cylinder axes${holeN ? ` (${holeN})` : ''}`}
        hint="Drill/bore axes from the analytic surfaces." checked={setup.holeAxes}
        onChange={(v) => patch({ holeAxes: v })} />

      <div className="h-px bg-zinc-950/10 dark:bg-white/10" />

      <div>
        <label className={labelCls}>Add manual axis</label>
        <div className="mt-2 flex items-center gap-2">
          <Input type="number" step="0.1" aria-label="x" value={ax} onChange={(e) => setAx(e.target.value)} />
          <Input type="number" step="0.1" aria-label="y" value={ay} onChange={(e) => setAy(e.target.value)} />
          <Input type="number" step="0.1" aria-label="z" value={az} onChange={(e) => setAz(e.target.value)} />
          <Button outline onClick={addAxis} aria-label="Add axis"><Plus data-slot="icon" /></Button>
        </div>
      </div>

      <div>
        <label className={labelCls}>Averaged normal from BREP faces</label>
        <p className={clsx('mt-1', hintCls)}>
          Pick whole BREP faces in the viewer — their mean normal becomes one direction (for curved walls).
        </p>
        <div className="mt-2 flex items-center gap-2">
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
      </div>

      {(setup.manual.length > 0 || setup.brepGroups.length > 0) && (
        <div>
          <div className="mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">Added directions</div>
          <ul className="flex flex-col gap-1">
            {setup.manual.map((v, i) => (
              <li key={`m${i}`} className="flex items-center gap-2 text-sm/5 text-zinc-700 dark:text-zinc-300">
                <Swatch source="manual" />
                <span className="flex-1">[{fmt(v)}]</span>
                <button type="button" aria-label="remove"
                  className="text-zinc-400 hover:text-red-600"
                  onClick={() => patch({ manual: setup.manual.filter((_, j) => j !== i) })}>
                  <X className="size-3.5" />
                </button>
              </li>
            ))}
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
        </div>
      )}

      {setup.suppressed.length > 0 && (
        <Button plain onClick={() => patch({ suppressed: [] })}>
          <EyeOff data-slot="icon" /> Restore {setup.suppressed.length} hidden
        </Button>
      )}

      {stats && (
        <div className="mt-auto">
          <div className="mb-1.5 text-xs/5 font-medium text-zinc-500 dark:text-zinc-400">In view</div>
          <p className="whitespace-pre-wrap text-xs/5 text-zinc-500 dark:text-zinc-400">{stats}</p>
        </div>
      )}
    </div>
  );
}
