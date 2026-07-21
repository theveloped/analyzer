import { Disclosure, DisclosureButton, DisclosurePanel } from '@headlessui/react';
import clsx from 'clsx';
import { BookmarkPlus, ChevronDown, RotateCw, Settings2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from '../../catalyst/button';
import { Input } from '../../catalyst/input';
import { Select } from '../../catalyst/select';
import { useStore } from '../../state/store';
import {
  BOUND_UNITS, fieldDescriptor, fieldLensCompute, fieldStats,
  latestResult, resolveBound, type BandBound, type BoundUnit,
  type FieldLensDef, type FieldStats,
} from '../fieldLenses';
import { StatusBadge } from '../components/status';
import { useV2 } from '../store';
import { ComputeInput } from './computeFields';
import {
  saveLensCheck, useActiveFieldLens, useActiveLens, usePlanSection,
} from './hooks';
import { useBusy } from './run';
import { runAnalysisJob } from '../../viewer/jobs';

const hintCls = 'text-xs/5 text-zinc-500 dark:text-zinc-400';
const sectionCls = 'text-xs/5 font-medium text-zinc-500 dark:text-zinc-400';

/** The lens's compute payload: v2 store overrides on top of the defaults. */
function currentCompute(def: FieldLensDef): Record<string, unknown> {
  const stored = useV2.getState().compute[def.modeId] ?? {};
  return { ...fieldLensCompute(def), ...stored };
}

/** Whether a re-run would produce anything new: some compute knob differs
 * from the stored result's params (numbers compared loosely — JSON strips
 * float-ness) or the result is stale. */
function computeChanged(
  def: FieldLensDef, compute: Record<string, unknown>,
  resultParams: Record<string, unknown> | undefined, stale: boolean,
): boolean {
  if (stale) return true;
  if (!resultParams) return true;
  return def.computeFields.some((f) => {
    const a = compute[f.key];
    const b = resultParams[f.key];
    if (a == null || b == null) return (a ?? null) !== (b ?? null);
    return Number(a) !== Number(b) && String(a) !== String(b);
  });
}

function BoundRow({ label, bound, onChange, fieldUnit, resolved }: {
  label: string; bound: BandBound; onChange: (b: BandBound) => void;
  fieldUnit: string; resolved: number | null;
}) {
  return (
    <div className="mt-1.5 grid grid-cols-[2rem_4.5rem_1fr_2.75rem] items-center gap-1.5">
      <span className="text-xs/5 text-zinc-500 dark:text-zinc-400">{label}</span>
      <Input type="number" step="any" placeholder="—"
        value={bound.value}
        onChange={(e) => onChange({ ...bound, value: e.target.value })}
        aria-label={`band ${label}`} />
      <Select value={bound.unit}
        onChange={(e) => onChange({ ...bound, unit: e.target.value as BoundUnit })}
        aria-label={`band ${label} unit`}>
        {BOUND_UNITS(fieldUnit).map((u) => (
          <option key={u.id} value={u.id}>{u.label}</option>
        ))}
      </Select>
      <span className="text-right text-[11px]/4 tabular-nums text-zinc-400"
        title={`resolved bound (${fieldUnit})`}>
        {resolved == null ? '—' : `${+resolved.toFixed(2)}`}
      </span>
    </div>
  );
}

function BandSection({ def, stats }: { def: FieldLensDef; stats: FieldStats }) {
  const setParam = useStore((s) => s.setViewerParam);
  const partId = useStore((s) => s.partId);
  const section = usePlanSection();
  const [lo, setLo] = useState<BandBound>({ value: '', unit: 'abs' });
  const [hi, setHi] = useState<BandBound>({ value: '', unit: 'abs' });
  const [saving, setSaving] = useState(false);

  const rLo = resolveBound(lo, stats);
  const rHi = resolveBound(hi, stats);
  const active = rLo != null || rHi != null;

  // the resolved bounds drive the highlight params: the heatmap itself
  // stays untouched, in-band faces paint the magenta selection color
  useEffect(() => {
    const fmt = (v: number | null) => (v == null ? '' : String(+v.toFixed(4)));
    setParam(def.process, def.bandLoParam, fmt(rLo));
    setParam(def.process, def.bandHiParam, fmt(rHi));
  }, [def, rLo, rHi, setParam]);

  const bandText = !active ? 'off'
    : rLo != null && rHi != null
      ? `${rLo.toFixed(2)} – ${rHi.toFixed(2)} ${def.unit}`
      : rLo != null ? `≥ ${rLo.toFixed(2)} ${def.unit}`
      : `≤ ${rHi!.toFixed(2)} ${def.unit}`;

  const checkId = `chk-${def.modeId}`;
  const saved = section?.plan.checks.find((c) => c.id === checkId);

  const save = () => {
    setSaving(true);
    void saveLensCheck(def, {
      band: [rLo, rHi],
      band_def: { lo, hi },
      threshold: def.flagDirection === 'below' ? (rLo ?? rHi) : (rHi ?? rLo),
      unit: def.unit,
    }, currentCompute(def)).finally(() => setSaving(false));
  };

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <span className={sectionCls}>Highlight band</span>
        {active && (
          <button type="button"
            onClick={() => { setLo({ value: '', unit: 'abs' }); setHi({ value: '', unit: 'abs' }); }}
            className="text-[11px]/4 text-zinc-400 transition hover:text-zinc-600 dark:hover:text-zinc-200">
            clear
          </button>
        )}
      </div>
      <BoundRow label="from" bound={lo} onChange={setLo}
        fieldUnit={def.unit} resolved={rLo} />
      <BoundRow label="to" bound={hi} onChange={setHi}
        fieldUnit={def.unit} resolved={rHi} />
      <p className={clsx('mt-1.5', hintCls)}>
        {active ? (
          <>
            <span className="mr-1 inline-block size-2.5 translate-y-px rounded-[3px]"
              style={{ background: 'rgb(255 38 166)' }} />
            highlighting {bandText} — the heatmap underneath is unchanged.
          </>
        ) : 'Blank bounds are open-ended: set only “from” for a floor, only “to” for a cap.'}
        {' '}Field spans {stats.min.toFixed(2)} – {stats.max.toFixed(2)} {def.unit}
        {' '}(mean {stats.mean.toFixed(2)}, p50 {stats.p50.toFixed(2)},
        p95 {stats.p95.toFixed(2)}).
      </p>
      <Button outline onClick={save} className="mt-2 w-full"
        disabled={saving || !partId || !active}>
        <BookmarkPlus data-slot="icon" />
        {saved ? 'Update the saved check' : 'Save band as check'}
      </Button>
      {saved && (
        <p className={clsx('mt-1', hintCls)}>
          Saved as “{checkId}” at plan rev {section?.plan.revision}.
        </p>
      )}
    </div>
  );
}

/**
 * The field-lens side panel (spike): explain the view, advanced compute
 * knobs, a re-run that only arms when something actually changed, and the
 * clipping band — interpretation stays client-side until saved as a check.
 */
export function FieldLensRail() {
  const def = useActiveFieldLens();
  const lens = useActiveLens();
  if (!def || !lens) return null;
  return <Rail def={def} lensLabel={lens.label} lensBlurb={lens.blurb} />;
}

function Rail({ def, lensLabel, lensBlurb }: {
  def: FieldLensDef; lensLabel: string; lensBlurb?: string;
}) {
  const manifest = useStore((s) => s.manifest);
  const partId = useStore((s) => s.partId);
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const busy = useBusy();
  const compute = useV2((s) => s.compute[def.modeId]);
  void compute; // subscribe: re-arm the re-run button on knob changes
  const result = latestResult(manifest, def);
  const [fieldDist, setFieldDist] = useState<FieldStats | null>(null);

  useEffect(() => {
    setFieldDist(null);
    if (!manifest || !result) return;
    const desc = fieldDescriptor(manifest, result, def);
    if (!desc) return;
    let live = true;
    void fieldStats(desc).then((s) => { if (live) setFieldDist(s); });
    return () => { live = false; };
  }, [manifest, result, def]);

  const merged = currentCompute(def);
  const changed = computeChanged(def, merged, result?.params, !!result?.stale);
  const rerun = () => {
    if (!partId) return;
    runAnalysisJob(partId, def.process, def.analysis, merged)
      .catch((err) => useStore.getState().set({ error: String(err) }));
  };

  return (
    <div className="flex h-full w-72 shrink-0 flex-col gap-4 overflow-auto border-l border-zinc-950/5 bg-white p-4 dark:border-white/10 dark:bg-zinc-900">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">{lensLabel}</h2>
          {busy ? <StatusBadge status="active">computing…</StatusBadge>
            : !result ? <StatusBadge status="neutral">not run</StatusBadge>
            : result.stale ? <StatusBadge status="warning">stale</StatusBadge>
            : <StatusBadge status="good">current</StatusBadge>}
        </div>
        <p className={clsx('mt-1', hintCls)}>
          {lensBlurb ?? 'Plain field heatmap over the real data range.'}
          {!result && !busy && ' Runs automatically with plain defaults.'}
        </p>
      </div>

      {def.computeFields.length > 0 && (
        <Disclosure>
          {({ open }) => (
            <div>
              <DisclosureButton className="flex w-full items-center justify-between rounded-lg px-1 py-1 text-xs/5 font-medium text-zinc-500 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white">
                <span className="flex items-center gap-1.5">
                  <Settings2 className="size-3.5" /> Advanced
                </span>
                <ChevronDown className={clsx('size-3.5 transition-transform', open && 'rotate-180')} />
              </DisclosureButton>
              <DisclosurePanel className="mt-2 flex flex-col gap-4">
                {def.computeFields.map((field) => (
                  <ComputeInput key={field.key} computeId={def.modeId} field={field} />
                ))}
              </DisclosurePanel>
            </div>
          )}
        </Disclosure>
      )}

      <Button onClick={rerun} disabled={busy || !changed} className="w-full"
        title={changed ? undefined : 'nothing changed since the stored run'}>
        <RotateCw data-slot="icon" className={busy ? 'animate-spin' : undefined} />
        {busy ? 'Computing…' : result ? 'Re-run analysis' : 'Run analysis'}
      </Button>

      <div className="h-px bg-zinc-950/10 dark:bg-white/10" />

      {result && fieldDist ? (
        <BandSection def={def} stats={fieldDist} />
      ) : (
        <p className={hintCls}>
          {busy ? 'Computing the field…' : result
            ? 'Loading the field distribution…'
            : 'The clipping band appears once the field exists.'}
        </p>
      )}

      <div>
        <div className={clsx(sectionCls, 'mb-1.5')}>In view</div>
        {error ? (
          <p className="whitespace-pre-wrap text-xs/5 text-red-600 dark:text-red-500">⚠ {error}</p>
        ) : (
          <p className="whitespace-pre-wrap text-xs/5 text-zinc-500 dark:text-zinc-400">{stats}</p>
        )}
      </div>
    </div>
  );
}
